#!/usr/bin/env python3
"""Build script for the system-spec HTML viewer (v2).

Walks the system-spec/ directory which has a three-level structure:
  category/ (functionality, architecture, testing, observability)
    section/ (identity-statement directories with _section.yaml)
      leaf.yaml (invariant files)

Produces a JSON blob and inlines it into index.html.

Usage:
    python build.py

Idempotent: running multiple times produces the same output.
"""
import datetime
import json
import re
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("Error: PyYAML not installed. Run: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

SPEC_DIR = Path(__file__).parent
INDEX_HTML = SPEC_DIR / "index.html"

# Categories rendered in this order
CATEGORY_ORDER = [
    "functionality",
    "architecture",
    "testing",
    "observability",
]


def read_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def clean_str(value: object) -> str:
    """Return a clean string from a YAML value (may be a multiline scalar)."""
    if value is None:
        return ""
    return str(value).strip()


def build_spec() -> dict:
    # Read optional top-level meta
    meta_path = SPEC_DIR / "_meta.yaml"
    meta = read_yaml(meta_path) if meta_path.exists() else {}

    # Collect category directories in defined order
    all_dirs = {d.name: d for d in SPEC_DIR.iterdir() if d.is_dir()}
    ordered_names = [n for n in CATEGORY_ORDER if n in all_dirs]
    extra_names = sorted(n for n in all_dirs if n not in CATEGORY_ORDER)
    category_names = ordered_names + extra_names

    categories = []
    for cat_idx, cat_name in enumerate(category_names, 1):
        cat_dir = all_dirs[cat_name]

        # Category directories contain section subdirectories
        section_dirs = sorted(
            d for d in cat_dir.iterdir()
            if d.is_dir() and (d / "_section.yaml").exists()
        )
        if not section_dirs:
            continue

        sections = []
        for sec_idx, section_dir in enumerate(section_dirs, 1):
            section_data = read_yaml(section_dir / "_section.yaml")
            section_id = f"{cat_name}/{section_dir.name}"
            section_num = f"{cat_idx}.{sec_idx}"
            section: dict = {
                "id": section_id,
                "number": section_num,
                "title": clean_str(section_data.get("title", section_dir.name)),
                "principle": clean_str(section_data.get("principle", "")),
                "description": clean_str(section_data.get("description", "")),
                "category": cat_name,
                "leaves": [],
            }

            # Leaf files: *.yaml, not starting with _
            leaf_files = sorted(
                f
                for f in section_dir.iterdir()
                if f.is_file() and f.suffix == ".yaml" and not f.name.startswith("_")
            )
            for leaf_idx, leaf_path in enumerate(leaf_files, 1):
                leaf_data = read_yaml(leaf_path)
                leaf_num = f"{section_num}.{leaf_idx}"
                leaf: dict = {
                    "id": f"{section_id}/{leaf_path.stem}",
                    "number": leaf_num,
                    "title": clean_str(leaf_data.get("title", leaf_path.stem)),
                    "description": clean_str(leaf_data.get("description", "")),
                    "invariants": [],
                }
                for inv_idx, inv in enumerate(leaf_data.get("invariants", []), 1):
                    implemented = bool(inv.get("implemented", False))
                    tested = bool(inv.get("tested", False))
                    inv_num = f"{leaf_num}.{inv_idx}"
                    leaf["invariants"].append(
                        {
                            "id": clean_str(inv.get("id", "")),
                            "number": inv_num,
                            "description": clean_str(inv.get("description", "")),
                            "rationale": clean_str(inv.get("rationale", "")),
                            "source": clean_str(inv.get("source", "")),
                            "implemented": implemented,
                            "tested": tested,
                            "test": inv.get("test"),
                            "category": cat_name,
                            "section": section_dir.name,
                            "leaf": leaf_path.stem,
                        }
                    )
                section["leaves"].append(leaf)

            sections.append(section)

        categories.append({
            "id": cat_name,
            "number": str(cat_idx),
            "title": cat_name.replace("-", " ").title(),
            "sections": sections,
        })

    return {"meta": meta, "categories": categories}


def inject_into_html(spec: dict) -> None:
    if not INDEX_HTML.exists():
        print(f"Error: {INDEX_HTML} not found", file=sys.stderr)
        sys.exit(1)

    def _default(obj: object) -> str:
        if isinstance(obj, (datetime.date, datetime.datetime)):
            return obj.isoformat()
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

    spec_json = json.dumps(spec, separators=(",", ":"), ensure_ascii=False, default=_default)
    data_block = (
        f"<!-- SPEC_DATA_START -->\n"
        f"<script>const SPEC_DATA = {spec_json};</script>\n"
        f"<!-- SPEC_DATA_END -->"
    )

    html = INDEX_HTML.read_text(encoding="utf-8")
    pattern = r"<!-- SPEC_DATA_START -->.*?<!-- SPEC_DATA_END -->"
    if not re.search(pattern, html, re.DOTALL):
        print(
            "Error: SPEC_DATA_START/SPEC_DATA_END markers not found in index.html",
            file=sys.stderr,
        )
        sys.exit(1)

    new_html = re.sub(pattern, lambda _: data_block, html, flags=re.DOTALL)
    INDEX_HTML.write_text(new_html, encoding="utf-8")


def print_summary(spec: dict) -> None:
    all_invariants = [
        inv
        for cat in spec["categories"]
        for section in cat["sections"]
        for leaf in section["leaves"]
        for inv in leaf["invariants"]
    ]
    total = len(all_invariants)
    tested = sum(1 for inv in all_invariants if inv["tested"])
    implemented = sum(1 for inv in all_invariants if inv["implemented"] and not inv["tested"])
    aspirational = sum(1 for inv in all_invariants if not inv["implemented"] and not inv["tested"])
    total_sections = sum(len(cat["sections"]) for cat in spec["categories"])
    cats = len(spec["categories"])
    print(
        f"Built index.html: {cats} categories, {total_sections} sections, "
        f"{total} invariants ({tested} tested, {implemented} implemented, {aspirational} aspirational)"
    )
    for cat in spec["categories"]:
        cat_invs = sum(
            len(leaf["invariants"])
            for s in cat["sections"]
            for leaf in s["leaves"]
        )
        print(f"  {cat['title']}: {len(cat['sections'])} sections, {cat_invs} invariants")


def main() -> None:
    spec = build_spec()
    inject_into_html(spec)
    print_summary(spec)


if __name__ == "__main__":
    main()
