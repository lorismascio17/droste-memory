"""Local script shim for the Droste CLI."""

from __future__ import annotations

from core.droste_cli import main


if __name__ == "__main__":
    raise SystemExit(main())
