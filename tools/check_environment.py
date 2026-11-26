"""Check the selected interpreter, exact direct versions and optional Neo4j connectivity."""

import argparse
import json
import sys
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def inspect_environment(*, connect=False):
    root = Path(__file__).resolve().parents[1]
    mismatches = []
    installed = {}
    for filename in ("requirements.txt", "requirements-skills.txt"):
        for line in (root / filename).read_text().splitlines():
            if "==" not in line or line.startswith("#"):
                continue
            name, expected = line.split("==", 1)
            try:
                actual = version(name)
            except PackageNotFoundError:
                actual = None
            installed[name] = actual
            if actual != expected:
                mismatches.append({"package": name, "expected": expected, "actual": actual})
    result = {"python": sys.executable, "versions": installed, "mismatches": mismatches}
    try:
        import neo4j

        from db.graph_store import GraphStore

        result["neo4j_import"] = hasattr(neo4j, "GraphDatabase")
        result["neo4j_module"] = neo4j.__file__
        if connect:
            graph = GraphStore()
            try:
                graph.verify_and_prepare()
                result["neo4j_connectivity"] = True
            finally:
                graph.close()
    except (ImportError, RuntimeError, KeyError) as exc:
        result["neo4j_import"] = False
        result["neo4j_error"] = str(exc)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connect-neo4j", action="store_true")
    args = parser.parse_args()
    result = inspect_environment(connect=args.connect_neo4j)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result["mismatches"] or not result.get("neo4j_import"):
        print("Установите зависимости этим же Python и выберите .venv: Python: Select Interpreter в VS Code.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
