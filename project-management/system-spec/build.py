#!/usr/bin/env python3
"""Build script for the system-spec HTML viewer.

Walks the system-spec/ directory, reads all _section.yaml and leaf *.yaml
files, produces a JSON blob, and inlines it into index.html by replacing
everything between SPEC_DATA_START and SPEC_DATA_END comment markers.

Usage:
    python build.py

Idempotent: running multiple times produces the same output.
"""
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

# Sections rendered in this order in the sidebar
SECTION_ORDER = [
    "tasks",
    "git",
    "scheduler",
    "agents",
    "communication",
    "github",
    "observability",
    "configuration",
    "drafts",
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

    # Collect section directories in defined order, then alphabetically for any extras
    all_dirs = {d.name: d for d in SPEC_DIR.iterdir() if d.is_dir()}
    ordered_names = [n for n in SECTION_ORDER if n in all_dirs]
    extra_names = sorted(n for n in all_dirs if n not in SECTION_ORDER)
    section_names = ordered_names + extra_names

    sections = []
    for name in section_names:
        section_dir = all_dirs[name]
        section_yaml = section_dir / "_section.yaml"
        if not section_yaml.exists():
            continue

        section_data = read_yaml(section_yaml)
        section: dict = {
            "id": name,
            "title": clean_str(section_data.get("title", name)),
            "principle": clean_str(section_data.get("principle", "")),
            "description": clean_str(section_data.get("description", "")),
            "leaves": [],
        }

        # Leaf files: *.yaml, not starting with _
        leaf_files = sorted(
            f
            for f in section_dir.iterdir()
            if f.is_file() and f.suffix == ".yaml" and not f.name.startswith("_")
        )
        for leaf_path in leaf_files:
            leaf_data = read_yaml(leaf_path)
            leaf: dict = {
                "id": f"{name}/{leaf_path.stem}",
                "title": clean_str(leaf_data.get("title", leaf_path.stem)),
                "description": clean_str(leaf_data.get("description", "")),
                "invariants": [],
            }
            for inv in leaf_data.get("invariants", []):
                leaf["invariants"].append(
                    {
                        "id": clean_str(inv.get("id", "")),
                        "description": clean_str(inv.get("description", "")),
                        "rationale": clean_str(inv.get("rationale", "")),
                        "source": clean_str(inv.get("source", "")),
                        "status": clean_str(inv.get("status", "aspirational")),
                        "test": inv.get("test"),  # keep None as null in JSON
                        "section": name,
                        "leaf": leaf_path.stem,
                    }
                )
            section["leaves"].append(leaf)

        sections.append(section)

    return {"meta": meta, "sections": sections}


def inject_into_html(spec: dict) -> None:
    if not INDEX_HTML.exists():
        print(f"Error: {INDEX_HTML} not found", file=sys.stderr)
        sys.exit(1)

    spec_json = json.dumps(spec, separators=(",", ":"), ensure_ascii=False)
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

    new_html = re.sub(pattern, data_block, html, flags=re.DOTALL)
    INDEX_HTML.write_text(new_html, encoding="utf-8")


def print_summary(spec: dict) -> None:
    all_invariants = [
        inv
        for section in spec["sections"]
        for leaf in section["leaves"]
        for inv in leaf["invariants"]
    ]
    total = len(all_invariants)
    enforced = sum(1 for inv in all_invariants if inv["status"] == "enforced")
    aspirational = total - enforced
    sections = len(spec["sections"])
    print(
        f"Built index.html: {sections} sections, {total} invariants "
        f"({enforced} enforced, {aspirational} aspirational)"
    )


def main() -> None:
    spec = build_spec()
    inject_into_html(spec)
    print_summary(spec)


if __name__ == "__main__":
    main()
