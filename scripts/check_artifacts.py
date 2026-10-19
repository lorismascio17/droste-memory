"""Reject distributions that omit viewer assets or include live/private data."""
from pathlib import Path
import argparse
import tarfile
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=Path("dist"))
    directory = parser.parse_args().dist
    wheels = list(directory.glob("*.whl"))
    sources = list(directory.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise SystemExit("Use a clean dist directory containing one wheel and one sdist")
    with zipfile.ZipFile(wheels[0]) as archive:
        wheel_names = archive.namelist()
    with tarfile.open(sources[0]) as archive:
        source_names = archive.getnames()
    required = {"visualizer/cockpit.html", "visualizer/templates/index.html", "visualizer/demo_graph.json", "core/storage_paths.py"}
    assert required <= set(wheel_names), required - set(wheel_names)
    for name in wheel_names + source_names:
        parts = Path(name).parts
        assert not {".droste", ".tmp", ".venv"}.intersection(parts), name
        assert Path(name).name not in {"graph.json", "status.json", "context.json", "droste_memory_db.json", "droste_embed_cache.json", "droste_file_registry.json"}, name
        assert not name.endswith(".log"), name
    print("Distribution contents verified: viewer assets present; live database and generated data excluded")


if __name__ == "__main__":
    main()
