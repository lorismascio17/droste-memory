"""Conservative Python call resolution; source parsing never executes code.

Resolve lexical definitions, import aliases and self/cls members. Dynamic
receivers, shadowed names and ambiguous modules remain unresolved instead of
being connected to an arbitrary same-named symbol elsewhere in the project.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .droste_engine import DrosteNode


@dataclass(frozen=True)
class ImportRef:
    module: str
    member: str = ""


@dataclass(eq=False)
class Scope:
    tree: ast.AST
    path: str
    module: str
    parent: Scope | None = None
    node: DrosteNode | None = None
    bindings: dict[str, Any] = field(default_factory=dict)
    qualname: str = ""
    assignments: set[str] = field(default_factory=set)


class PythonResolver:
    def __init__(self, root: str, texts: dict[str, str], nodes: list[DrosteNode]):
        self.root = Path(root)
        self.modules: dict[str, list[Scope]] = {}
        self.exact_modules: dict[str, Scope] = {}
        self.scopes: list[Scope] = []
        self.unresolved_calls = 0
        self._nodes = {(n.source_path, n.line_start): n for n in nodes}
        for path, text in sorted(texts.items()):
            if Path(path).suffix not in {".py", ".pyi"}:
                continue
            try:
                tree = ast.parse(text)
            except (SyntaxError, ValueError):
                continue
            parts = list(Path(path).relative_to(self.root).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts.pop()
            module = ".".join(parts)
            scope = Scope(tree, path, module)
            if module not in self.exact_modules or Path(path).suffix == ".py":
                self.exact_modules[module] = scope
            # src layouts and package subdirectory indexing use suffix aliases;
            # a collision makes the alias unusable rather than picking a file.
            for offset in range(len(parts)):
                self.modules.setdefault(".".join(parts[offset:]), []).append(scope)
            self._build(scope)

    @staticmethod
    def _bind(scope: Scope, name: str, value: Any) -> None:
        if name in scope.bindings and scope.bindings[name] != value:
            scope.bindings[name] = None
        else:
            scope.bindings[name] = value

    def _build(self, scope: Scope) -> None:
        self.scopes.append(scope)
        tree = scope.tree
        if isinstance(tree, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = tree.args
            for arg in [*args.posonlyargs, *args.args, *args.kwonlyargs, args.vararg, args.kwarg]:
                if arg:
                    self._bind(scope, arg.arg, None)

        def visit(item: ast.AST) -> None:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualified = ".".join(filter(None, [scope.qualname, item.name]))
                child = Scope(item, scope.path, scope.module, scope, self._nodes.get((scope.path, item.lineno)), qualname=qualified)
                self._bind(scope, item.name, child)
                self._build(child)
                return
            if isinstance(item, ast.Import):
                for alias in item.names:
                    name = alias.asname or alias.name.split(".")[0]
                    self._bind(scope, name, ImportRef(alias.name if alias.asname else name))
                return
            if isinstance(item, ast.ImportFrom):
                module = item.module or ""
                if item.level:
                    package = scope.module.split(".")
                    if Path(scope.path).stem != "__init__":
                        package = package[:-1]
                    keep = len(package) - item.level + 1
                    module = ".".join([*package[:max(0, keep)], *filter(None, [module])])
                for alias in item.names:
                    if alias.name != "*":
                        self._bind(scope, alias.asname or alias.name, ImportRef(module, alias.name))
                return
            if isinstance(item, ast.Lambda):
                return
            if isinstance(item, ast.Name) and isinstance(item.ctx, (ast.Store, ast.Del)):
                scope.assignments.add(item.id)
                self._bind(scope, item.id, None)
            for child in ast.iter_child_nodes(item):
                visit(child)

        for statement in getattr(tree, "body", []):
            visit(statement)

    def _module(self, name: str) -> Scope | None:
        if name in self.exact_modules:
            return self.exact_modules[name]
        candidates = list({candidate.module: self.exact_modules[candidate.module]
                           for candidate in self.modules.get(name, [])}.values())
        return candidates[0] if len(candidates) == 1 else None

    def _import(self, reference: ImportRef, seen: set[tuple[str, str]]) -> Any:
        key = (reference.module, reference.member)
        if key in seen:
            return None
        seen = seen | {key}
        module = self._module(reference.module)
        if reference.member:
            value = module.bindings.get(reference.member) if module else None
            if value is not None:
                resolved = self._import(value, seen) if isinstance(value, ImportRef) else value
                if resolved is not None:
                    return resolved
            return self._module(".".join(filter(None, key)))
        if module is not None:
            return module
        return reference if any(name.startswith(reference.module + ".") for name in self.modules) else None

    def _name(self, scope: Scope, name: str) -> Any:
        current: Scope | None = scope
        while current:
            # A method's bare names do not resolve in its class namespace.
            if current is scope or not isinstance(current.tree, ast.ClassDef):
                if name in current.bindings:
                    value = current.bindings[name]
                    return self._import(value, set()) if isinstance(value, ImportRef) else value
            current = current.parent
        return None

    def _expression(self, scope: Scope, expression: ast.AST) -> Any:
        if isinstance(expression, ast.Name):
            return self._name(scope, expression.id)
        if not isinstance(expression, ast.Attribute):
            return None
        receiver = expression.value
        if isinstance(receiver, ast.Name) and receiver.id in {"self", "cls"}:
            parent = scope.parent
            args = getattr(scope.tree, "args", None)
            positional = [*args.posonlyargs, *args.args] if args else []
            decorators = [getattr(d, "id", getattr(d, "attr", "")) for d in getattr(scope.tree, "decorator_list", [])]
            if "staticmethod" in decorators or receiver.id in scope.assignments:
                return None
            if parent and isinstance(parent.tree, ast.ClassDef) and positional and positional[0].arg == receiver.id:
                owner = parent
            else:
                return None
        else:
            owner = self._expression(scope, receiver)
        if isinstance(owner, ImportRef):
            return self._import(ImportRef(f"{owner.module}.{expression.attr}"), set())
        if not isinstance(owner, Scope):
            return None
        value = owner.bindings.get(expression.attr)
        if isinstance(value, ImportRef):
            return self._import(value, set())
        if expression.attr in owner.bindings:
            return value
        if isinstance(owner.tree, ast.Module):
            return self._module(f"{owner.module}.{expression.attr}")
        return None

    @staticmethod
    def _calls(scope: Scope):
        def walk(item):
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                return
            if isinstance(item, ast.Call):
                yield item.func
            for child in ast.iter_child_nodes(item):
                yield from walk(child)
        if isinstance(scope.tree, ast.ClassDef):
            yield from scope.tree.bases
        else:
            for statement in getattr(scope.tree, "body", []):
                yield from walk(statement)

    def links(self) -> list[dict[str, Any]]:
        links: dict[tuple[str, str], dict[str, Any]] = {}
        for scope in self.scopes:
            if scope.node is None:
                continue
            for expression in self._calls(scope):
                lookup_scope = scope.parent if isinstance(scope.tree, ast.ClassDef) else scope
                target = self._expression(lookup_scope, expression) if lookup_scope else None
                if not isinstance(target, Scope) or target.node is None:
                    self.unresolved_calls += 1
                    continue
                if target.node.id == scope.node.id:
                    continue
                key = (scope.node.id, target.node.id)
                expression_text = ast.unparse(expression)
                link = links.setdefault(key, {
                    "from": key[0], "to": key[1], "type": "syntax_dependency",
                    "label": expression_text, "index_root": str(self.root),
                    "weight": 1.0, "resolution": "python_scope_import",
                    "evidence": [],
                })
                link["evidence"].append({"path": scope.path, "line": expression.lineno,
                                         "column": expression.col_offset, "expression": expression_text})
        return list(links.values())
