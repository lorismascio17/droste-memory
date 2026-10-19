"""Layered project ignore rules, without requiring an installed Git client."""
from pathlib import Path

from pathspec import GitIgnoreSpec


class SourceRules:
    def __init__(self, root: Path):
        self.root = root
        self.layers = {}
        self.overrides = self._load(root / ".drosteignore")

    @staticmethod
    def _load(path):
        try:
            return GitIgnoreSpec.from_lines(path.read_text(encoding="utf-8-sig").splitlines())
        except FileNotFoundError:
            return None

    def enter(self, directory: Path):
        layers = list(self.layers.get(directory.parent, []))
        spec = self._load(directory / ".gitignore")
        if spec is not None:
            layers.append((directory, spec))
        self.layers[directory] = layers

    def ignored(self, path: Path, is_directory=False):
        ignored = False
        suffix = "/" if is_directory else ""
        for base, spec in self.layers.get(path.parent, []):
            match = spec.check_file(path.relative_to(base).as_posix() + suffix)
            if match.include is not None:
                ignored = match.include
        if self.overrides is not None:
            match = self.overrides.check_file(path.relative_to(self.root).as_posix() + suffix)
            if match.include is not None:
                ignored = match.include
        return ignored
