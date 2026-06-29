"""Tree-sitter universal symbol + call-graph extraction.

Droste's causal-graph advantage (syntax_dependency wormholes) was Python-only:
every other language went through a regex that produced second-class
``dependency`` edges the engine ignores. This module gives non-Python languages
a real AST so their references become first-class ``syntax_dependency`` edges.

Resolution is name-based (collect the identifiers used inside a definition's
span; the ingester links names that match other definitions) — the same
approach Aider's repomap uses. Tree-sitter yields accurate identifiers where
regex was noisy (strings/comments) and missed call-chains.

Import is guarded: if tree-sitter is unavailable the ingester silently falls
back to the regex registry, so the zero-config boot never breaks.

NOTE: tree-sitter-language-pack ships a NON-standard method-based binding
(verified live): ``parser.parse(str)`` (wants str, not bytes); ``tree.root_node()``
is a METHOD; on a Node everything is a method — ``kind()``, ``child_count()``,
``child(i)``, ``child_by_field_name(name)``, ``start_byte()``, ``end_byte()``,
``parent()``. There is no ``.type``/``.children``/``.text``.
"""
from __future__ import annotations

from typing import Any

try:  # guarded: missing tree-sitter must not break indexing
    from tree_sitter_language_pack import get_parser as _get_parser
    _TS_OK = True
except Exception:  # pragma: no cover - depends on optional dependency
    _get_parser = None
    _TS_OK = False

# Extension -> tree-sitter grammar name. Each grammar's definition node kinds
# below were validated empirically (eval/ts_probe_langs.py) before enabling.
TS_LANG_BY_EXT: dict[str, str] = {
    ".dart": "dart",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".ts": "typescript", ".tsx": "tsx",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin", ".kts": "kotlin",
    # C / C++: .h treated as C, .hpp/.hh/.hxx as C++ (the clang/Linux default).
    ".c": "c", ".h": "c",
    ".cc": "cpp", ".cpp": "cpp", ".cxx": "cpp", ".c++": "cpp",
    ".hpp": "cpp", ".hh": "cpp", ".hxx": "cpp",
}

# grammar -> {definition node kind: Droste kind}. Curated allowlist: only true
# top-level/member definitions, NOT fields/params/enum-members (which also expose
# a 'name' field but are not symbols). For Dart, method_signature is omitted (an
# empty-named wrapper) in favour of its inner function/getter/setter signatures.
_DEF_KINDS: dict[str, dict[str, str]] = {
    "dart": {
        "class_definition": "class", "mixin_declaration": "class",
        "enum_declaration": "class", "extension_declaration": "class",
        "function_signature": "function", "getter_signature": "function",
        "setter_signature": "function", "constructor_signature": "function",
        "factory_constructor_signature": "function",
    },
    "javascript": {
        "class_declaration": "class",
        "function_declaration": "function", "generator_function_declaration": "function",
        "method_definition": "function",
    },
    "typescript": {
        "class_declaration": "class", "abstract_class_declaration": "class",
        "interface_declaration": "class", "enum_declaration": "class",
        "type_alias_declaration": "class",
        "function_declaration": "function", "generator_function_declaration": "function",
        "method_definition": "function", "method_signature": "function",
    },
    "go": {
        "type_spec": "class",
        "function_declaration": "function", "method_declaration": "function",
    },
    "rust": {
        "struct_item": "class", "enum_item": "class", "trait_item": "class",
        "union_item": "class", "mod_item": "class", "type_item": "class",
        "function_item": "function", "function_signature_item": "function",
        "macro_definition": "function",
    },
    "java": {
        "class_declaration": "class", "interface_declaration": "class",
        "enum_declaration": "class", "record_declaration": "class",
        "annotation_type_declaration": "class",
        "method_declaration": "function", "constructor_declaration": "function",
    },
    "csharp": {
        "class_declaration": "class", "interface_declaration": "class",
        "struct_declaration": "class", "enum_declaration": "class",
        "record_declaration": "class",
        "method_declaration": "function", "constructor_declaration": "function",
    },
    "ruby": {
        "class": "class", "module": "class",
        "method": "function", "singleton_method": "function",
    },
    "php": {
        "class_declaration": "class", "interface_declaration": "class",
        "trait_declaration": "class", "enum_declaration": "class",
        "function_definition": "function", "method_declaration": "function",
    },
    "swift": {
        # In this grammar struct/enum/class/extension all parse as
        # class_declaration and expose a real `name` field (verified live), so
        # the default name_of() path handles them. struct_declaration /
        # enum_declaration are listed defensively for grammar versions that emit
        # the split kinds.
        "class_declaration": "class", "protocol_declaration": "class",
        "struct_declaration": "class", "enum_declaration": "class",
        "function_declaration": "function", "protocol_function_declaration": "function",
    },
    "kotlin": {
        # class_declaration covers class / interface / enum class; the name is a
        # `type_identifier` child (no `name` field). object_declaration is a
        # singleton; function_declaration's name is a `simple_identifier` child.
        # All handled by name_of()'s identifier-child fallback.
        "class_declaration": "class", "object_declaration": "class",
        "function_declaration": "function",
    },
    "c": {
        # struct/union/enum_specifier expose a `name` field; function_definition
        # buries its name in the declarator chain (name_of descends it).
        "struct_specifier": "class", "union_specifier": "class",
        "enum_specifier": "class",
        "function_definition": "function",
    },
    "cpp": {
        "class_specifier": "class", "struct_specifier": "class",
        "union_specifier": "class", "enum_specifier": "class",
        "namespace_definition": "class",
        "function_definition": "function",
    },
}
# tsx shares typescript's definition kinds.
_DEF_KINDS["tsx"] = _DEF_KINDS["typescript"]

# Grammars where a definition node is only the SIGNATURE and the body is a
# following sibling (so the span must be extended to capture body references).
# Declaration-style grammars (js/ts/go/rust/java/...) already include the body.
_NEEDS_BODY_EXTENSION = {"dart"}
_BODY_KINDS = {"function_body", "block", "statement_block"}


def _is_ident(kind: str) -> bool:
    """A leaf identifier-ish node whose text is a potential reference name.
    Covers identifier/type_identifier/field_identifier/property_identifier across
    grammars, plus ruby `constant`, kotlin/swift `simple_identifier`, php `name`."""
    return kind.endswith("identifier") or kind in {"constant", "simple_identifier", "name"}

_parsers: dict[str, Any] = {}


def available() -> bool:
    return _TS_OK


def supported_ext(ext: str) -> bool:
    return _TS_OK and ext.lower() in TS_LANG_BY_EXT


def _parser(lang: str):
    parser = _parsers.get(lang)
    if parser is None:
        parser = _get_parser(lang)
        _parsers[lang] = parser
    return parser


def _kids(node) -> list:
    return [node.child(i) for i in range(node.child_count())]


def extract_symbols(text: str, ext: str) -> list[dict[str, Any]] | None:
    """Return symbol dicts (kind, name, line_start, line_end, references,
    ts_based=True) for a supported language, or None to fall back to regex."""
    if not supported_ext(ext):
        return None
    lang = TS_LANG_BY_EXT[ext.lower()]
    try:
        root = _parser(lang).parse(text).root_node()
    except Exception:
        return None

    b = text.encode("utf-8")
    defkinds = _DEF_KINDS[lang]

    def line_of(byte_off: int) -> int:
        return b.count(b"\n", 0, byte_off) + 1

    def node_text(node) -> str:
        return b[node.start_byte():node.end_byte()].decode("utf-8", "replace")

    # Identifier leaf kinds that can serve as a definition's name, ordered so a
    # declarator descent / child scan returns the most specific one.
    _NAME_LEAF_KINDS = (
        "identifier", "field_identifier", "type_identifier",
        "qualified_identifier", "simple_identifier",
        "destructor_name", "operator_name",
    )

    def name_of(node) -> str | None:
        # 1) Direct `name` field — swift, c/cpp struct/class/namespace, most
        #    declaration-style grammars.
        nm = node.child_by_field_name("name")
        if nm is not None:
            return node_text(nm)

        # 2) C / C++ function_definition: the identifier is nested inside the
        #    declarator chain (function_declarator -> ... -> identifier), often
        #    wrapped by pointer_/reference_/parenthesized_ declarators.
        decl = node.child_by_field_name("declarator")
        hops = 0
        while decl is not None and hops < 8:
            if decl.kind() in _NAME_LEAF_KINDS:
                return node_text(decl)
            nxt = decl.child_by_field_name("declarator")
            if nxt is None:
                for child in _kids(decl):
                    if child.kind() in _NAME_LEAF_KINDS:
                        return node_text(child)
                break
            decl = nxt
            hops += 1

        # 3) Kotlin (and any grammar with no name field): the first identifier-ish
        #    child is the symbol name (type_identifier for classes,
        #    simple_identifier for functions).
        for child in _kids(node):
            if child.kind() in _NAME_LEAF_KINDS:
                return node_text(child)
        return None

    extend_body = lang in _NEEDS_BODY_EXTENSION

    def full_span(node, symbol_kind: str) -> tuple[int, int]:
        """Return a definition span that captures the executable body when the
        grammar exposes it separately from the declaration/signature node.

        Most grammars include the body inside the definition node, but some
        expose a one-line signature with the block in a child or adjacent sibling.
        The universal fallback below walks the AST instead of special-casing
        every language, so Dart/TS/Java/C++/Go-style one-line spans do not index
        as signature-only symbols.
        """
        start, end = node.start_byte(), node.end_byte()
        if not extend_body:
            span = (start, end)
        else:
            span = (start, end)
            try:
                parent = node.parent()
                if parent is not None:
                    siblings = _kids(parent)
                    idx = -1
                    for k, c in enumerate(siblings):
                        if c.start_byte() == start and c.end_byte() == end and c.kind() == node.kind():
                            idx = k
                            break
                    if idx >= 0:
                        for c in siblings[idx + 1:]:
                            k = c.kind()
                            if _is_body_like(k):
                                span = (start, _deepest_child_end(c))
                                break
                            if k == ";" or k in defkinds:
                                break
            except Exception:
                pass

        if (
            _is_code_like_definition(node.kind(), symbol_kind)
            and line_of(span[0]) == line_of(span[1])
        ):
            end = max(span[1], _deepest_child_end(node))
            if line_of(start) == line_of(end):
                end = max(end, _following_body_end(node, defkinds))
            return start, end
        return span

    def _is_code_like_definition(node_kind: str, symbol_kind: str) -> bool:
        if symbol_kind in {"function", "method", "block"}:
            return True
        return any(part in node_kind for part in ("function", "method", "definition"))

    def _is_body_like(node_kind: str) -> bool:
        return node_kind in _BODY_KINDS or any(part in node_kind for part in ("body", "block"))

    def _deepest_child_end(node) -> int:
        end = node.end_byte()
        try:
            for child in _kids(node):
                child_end = _deepest_child_end(child)
                if child_end > end:
                    end = child_end
        except Exception:
            pass
        return end

    def _following_body_end(node, defkinds: dict[str, str]) -> int:
        current = node
        try:
            while current is not None:
                parent = current.parent()
                if parent is None:
                    break
                siblings = _kids(parent)
                idx = -1
                for k, child in enumerate(siblings):
                    if (
                        child.start_byte() == current.start_byte()
                        and child.end_byte() == current.end_byte()
                        and child.kind() == current.kind()
                    ):
                        idx = k
                        break
                if idx >= 0:
                    for child in siblings[idx + 1:]:
                        kind = child.kind()
                        if _is_body_like(kind):
                            return _deepest_child_end(child)
                        if kind == ";" or kind in defkinds:
                            break
                current = parent
        except Exception:
            pass
        return node.end_byte()

    defs: list[dict[str, Any]] = []
    idents: list[tuple[str, int]] = []

    def walk(node) -> None:
        kind = node.kind()
        if kind in defkinds:
            name = name_of(node)
            if name:
                symbol_kind = defkinds[kind]
                start, end = full_span(node, symbol_kind)
                defs.append({"kind": symbol_kind, "name": name, "sb": start, "eb": end})
        if _is_ident(kind):
            idents.append((node_text(node), node.start_byte()))
        for child in _kids(node):
            walk(child)

    try:
        walk(root)
    except Exception:
        return None

    if not defs:
        return None  # nothing parsed cleanly -> let the regex registry try

    # Attribute each identifier to its innermost enclosing definition, then a
    # reference is any identifier name inside a def that names a *different*
    # symbol. O((d+i) log) sweep instead of O(d*i): AST def spans nest cleanly,
    # so walking idents in position order against a stack of currently-open defs
    # (sorted by start) leaves the innermost open def on top.
    ref_sets: dict[int, set[str]] = {id(d): set() for d in defs}
    defs_by_start = sorted(defs, key=lambda d: (d["sb"], -d["eb"]))
    idents.sort(key=lambda it: it[1])
    stack: list[dict[str, Any]] = []
    di = 0
    total = len(defs_by_start)
    for name, pos in idents:
        while di < total and defs_by_start[di]["sb"] <= pos:
            stack.append(defs_by_start[di])
            di += 1
        while stack and stack[-1]["eb"] <= pos:
            stack.pop()
        if stack:
            inner = stack[-1]
            if inner["sb"] <= pos < inner["eb"] and name != inner["name"]:
                ref_sets[id(inner)].add(name)

    symbols: list[dict[str, Any]] = []
    for d in defs:
        symbols.append({
            "kind": d["kind"],
            "name": d["name"],
            "line_start": line_of(d["sb"]),
            "line_end": line_of(d["eb"]),
            "references": sorted(ref_sets[id(d)]),
            "ts_based": True,
        })
    symbols.sort(key=lambda s: (s["line_start"], s["name"]))
    return symbols
