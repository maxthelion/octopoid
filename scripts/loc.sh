#!/bin/bash
# Line count of source files, ordered by largest
find orchestrator packages/client/src tests .octopoid/agents .claude/commands commands \
  -type f \( -name '*.py' -o -name '*.ts' -o -name '*.tsx' -o -name '*.js' -o -name '*.yaml' -o -name '*.yml' -o -name '*.md' -o -name '*.sh' \) \
  ! -path '*/node_modules/*' ! -path '*/__pycache__/*' ! -path '*/dist/*' \
  | xargs wc -l 2>/dev/null \
  | sort -rn \
  | head -60
