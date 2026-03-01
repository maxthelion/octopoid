# Install qmd for semantic search over project documents

**Captured:** 2026-03-01
**Author:** human

## Raw

> Install https://github.com/tobi/qmd in the project. Point it at tasks, drafts and invariant specs.

## Idea

Install [qmd](https://github.com/tobi/qmd) — a local, on-device search engine for markdown documents — and configure it to index our project management documents. qmd combines BM25 full-text search, vector semantic search, and LLM re-ranking, all running locally via GGUF models.

Three collections to index:

1. **Drafts** — `project-management/drafts/` (~200 markdown files). Find related drafts, check for duplicates, trace how ideas evolved.
2. **Tasks** — `.octopoid/tasks/` (task markdown files created by `create_task()`). Search across task descriptions, acceptance criteria, and context.
3. **System spec** — `project-management/system-spec/` (YAML invariant files + section files). Search invariants by concept, find which invariants cover a given behaviour.

qmd also exposes an MCP server with tools (`qmd_search`, `qmd_vector_search`, `qmd_deep_search`, `qmd_get`), which means Claude Code sessions could search project documents via natural language during conversations.

## Invariants

- **qmd-collections-configured**: qmd has collections configured for drafts, tasks, and system-spec directories. Running `qmd collection list` shows all three.
- **qmd-mcp-available**: The qmd MCP server is configured in `.claude/settings.json` (or equivalent), making `qmd_search` and `qmd_deep_search` available as tools in Claude Code sessions.

## Context

The project has ~200 drafts, dozens of tasks, and a growing system-spec tree. Finding related documents currently requires grep or manual memory. Semantic search would make it much faster to find prior art, check for duplicates, and trace decisions back to their source drafts.

The `/draft-idea` skill already checks for duplicates by scanning titles, but semantic search over full content would catch conceptual overlaps that title matching misses.

## Open Questions

- Should embeddings be regenerated automatically (e.g. as a git hook or scheduler job), or is manual `qmd embed` sufficient?
- Should the qmd MCP server be configured project-wide (`.claude/settings.json`) or per-user (`.claude/settings.local.json`)?
- Does qmd handle YAML files natively, or do we need to convert system-spec YAMLs to markdown first?
- Should task files on the server (fetched via SDK) also be indexed, or only local files?
- Install via npm or bun? (Project already uses npm for the client package.)

## Possible Next Steps

- Install qmd globally: `npm install -g @tobilu/qmd`
- Configure three collections: `qmd collection add project-management/drafts --name drafts`, same for tasks and system-spec
- Add context descriptions: `qmd context add qmd://drafts "Design proposals and ideas for the octopoid orchestrator"`
- Generate embeddings: `qmd embed`
- Configure MCP server in `.claude/settings.json`
- Test: `qmd query "how does the fixer handle intervention?"` should surface relevant drafts and invariants
