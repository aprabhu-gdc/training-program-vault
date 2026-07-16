"""Offline validation for the hand-authored PBIP project.

Checks, per file under TrainingBotAnalytics/:
- decodes as UTF-8 and has no BOM (Power BI requires UTF-8 without BOM)
- TMDL files are tab-indented (no line begins with a space)
- JSON-family files parse, and validate against their embedded $schema
  (schemas fetched from developer.microsoft.com and cached; network failures
  downgrade to a warning unless --strict)
- report hygiene: visual `name` matches its folder and is unique report-wide,
  `position` carries x/y/z/height/width, every queryState entity is a model
  table, and page names line up with pages.json

Exit code 0 = clean, 1 = errors (or warnings under --strict).

Run: python powerbi/validate_report.py [--strict]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent / "TrainingBotAnalytics"
JSON_SUFFIXES = {".json", ".pbir", ".pbism", ".pbip", ".platform"}
MODEL_TABLES = {"QueryEvents", "Feedback", "DimUser", "DimDate", "_Measures"}
POSITION_KEYS = {"x", "y", "z", "height", "width"}
SCHEMA_CACHE = Path(tempfile.gettempdir()) / "pbip-schema-cache"

errors: list[str] = []
warnings: list[str] = []


def _rel(path: Path) -> str:
    return str(path.relative_to(PROJECT_DIR.parent))


def _fetch_schema(url: str):
    import requests

    SCHEMA_CACHE.mkdir(parents=True, exist_ok=True)
    cached = SCHEMA_CACHE / re.sub(r"[^A-Za-z0-9.]+", "_", url)
    if cached.exists():
        return json.loads(cached.read_text(encoding="utf-8"))
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    cached.write_text(response.text, encoding="utf-8")
    return response.json()


def _validate_schema(path: Path, document: dict) -> None:
    schema_url = document.get("$schema")
    if not schema_url:
        return
    try:
        import jsonschema
        from referencing import Registry, Resource

        schema = _fetch_schema(schema_url)

        def retrieve(uri: str):
            return Resource.from_contents(_fetch_schema(uri))

        registry = Registry(retrieve=retrieve)
        validator_cls = jsonschema.validators.validator_for(schema)
        validator = validator_cls(schema, registry=registry)
        for error in validator.iter_errors(document):
            errors.append(f"{_rel(path)}: schema violation at {list(error.absolute_path)}: {error.message[:200]}")
    except Exception as exc:  # noqa: BLE001 - network/schema fetch is best-effort
        warnings.append(f"{_rel(path)}: could not schema-validate ({type(exc).__name__}: {exc})")


def _check_visuals() -> None:
    seen_names: dict[str, str] = {}
    for visual_path in sorted(PROJECT_DIR.glob("**/visuals/*/visual.json")):
        folder = visual_path.parent.name
        doc = json.loads(visual_path.read_text(encoding="utf-8-sig"))

        name = doc.get("name")
        if name != folder:
            errors.append(f"{_rel(visual_path)}: name {name!r} != folder {folder!r}")
        if name in seen_names:
            errors.append(f"{_rel(visual_path)}: duplicate visual name {name!r} (also {seen_names[name]})")
        seen_names[name] = folder

        position = doc.get("position") or {}
        missing = POSITION_KEYS - set(position)
        if missing:
            errors.append(f"{_rel(visual_path)}: position missing {sorted(missing)}")

        for match in re.finditer(r'"Entity"\s*:\s*"([^"]+)"', visual_path.read_text(encoding="utf-8-sig")):
            if match.group(1) not in MODEL_TABLES:
                errors.append(f"{_rel(visual_path)}: unknown entity {match.group(1)!r}")


def _check_pages() -> None:
    pages_json = PROJECT_DIR.glob("**/pages/pages.json")
    for pages_path in pages_json:
        doc = json.loads(pages_path.read_text(encoding="utf-8-sig"))
        declared = set(doc.get("pageOrder") or [])
        folders = {p.name for p in pages_path.parent.iterdir() if p.is_dir()}
        if declared != folders:
            errors.append(f"{_rel(pages_path)}: pageOrder {sorted(declared)} != page folders {sorted(folders)}")
        active = doc.get("activePageName")
        if active not in declared:
            errors.append(f"{_rel(pages_path)}: activePageName {active!r} not in pageOrder")
        for folder in folders:
            page_doc = json.loads((pages_path.parent / folder / "page.json").read_text(encoding="utf-8-sig"))
            if page_doc.get("name") != folder:
                errors.append(f"pages/{folder}/page.json: name {page_doc.get('name')!r} != folder")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--strict", action="store_true", help="Treat warnings (e.g. schema fetch failures) as errors")
    args = parser.parse_args(argv)

    if not PROJECT_DIR.is_dir():
        print(f"ERROR: {PROJECT_DIR} not found", file=sys.stderr)
        return 1

    for path in sorted(PROJECT_DIR.glob("**/*")):
        if not path.is_file():
            continue
        # Power BI Desktop's machine-local state (cache.abf is binary by design).
        if ".pbi" in path.parts:
            continue
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            errors.append(f"{_rel(path)}: has a UTF-8 BOM (must be UTF-8 without BOM)")
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            errors.append(f"{_rel(path)}: not valid UTF-8")
            continue

        if path.suffix == ".tmdl":
            for line_no, line in enumerate(text.splitlines(), start=1):
                if line.startswith(" "):
                    errors.append(f"{_rel(path)}:{line_no}: space-indented line (TMDL requires tabs)")
        elif path.suffix in JSON_SUFFIXES or path.name == ".platform":
            try:
                document = json.loads(text)
            except json.JSONDecodeError as exc:
                errors.append(f"{_rel(path)}: invalid JSON ({exc})")
                continue
            if isinstance(document, dict):
                _validate_schema(path, document)

    _check_visuals()
    _check_pages()

    for warning in warnings:
        print(f"WARNING: {warning}")
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)

    failed = bool(errors) or (args.strict and bool(warnings))
    print(f"\n{'FAILED' if failed else 'OK'}: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
