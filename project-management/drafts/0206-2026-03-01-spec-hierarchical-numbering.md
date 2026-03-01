# Add hierarchical numbering to spec items

**Captured:** 2026-03-01

## Raw

> Let's add numbers to items in functionality, each section is numbered, and the ones below too. We should be able to refer to point 3.4.2 etc

## Idea

Every item in the system spec tree gets a hierarchical number so you can refer to any item by a short numeric path. Categories are 1-4, sections within each are X.Y, leaves are X.Y.Z, and invariants are X.Y.Z.W. For example "3.2.1" means Testing > Spec drives tests > Coverage mapping.

This makes it possible to discuss spec items in conversation, PR comments, or task descriptions without spelling out the full path.

## Invariants

- **spec-items-have-numbers**: Every category, section, leaf, and invariant in the spec has a `number` field assigned by build.py based on its position in the tree. Numbers are stable within a build (deterministic ordering).
- **numbers-displayed-in-viewer**: The HTML viewer shows numbers in the sidebar tree, content headers, overview cards, and invariant cards.
- **numbers-are-searchable**: Typing a number like "1.3.2" in the search box finds the matching item.

## Context

Came up while working on v2 of the system spec. With 73 invariants across 4 categories and 19 sections, a compact reference scheme is needed. Draft 199 (tree structure) and 205 (identity-oriented tree) set up the hierarchy; this adds the numbering layer on top.

## Open Questions

- Should numbers be written back into the YAML files, or only computed at build time? Currently build-time only.
- If a section is inserted in the middle, all subsequent numbers shift. Is that acceptable, or should we use stable IDs for cross-references?

## Possible Next Steps

- Already implemented in build.py and index.html during this session.
- Consider whether task descriptions / PR comments should reference invariants by number or by ID.
