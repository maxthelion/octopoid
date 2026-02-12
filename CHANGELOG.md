# Changelog

All notable changes to this project will be documented in this file.

## [Unreleased]

### Added

- Command whitelist for IDE permission systems. Agents declare the shell commands they need (git, gh, npm, python) so IDEs can bulk-approve them at init time instead of prompting per-command. Configurable via `commands:` section in `agents.yaml`. Export with `orchestrator-permissions export --format claude-code`. (GH-7)
