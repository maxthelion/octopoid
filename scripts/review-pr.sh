#!/usr/bin/env bash
# Download a PR diff to /tmp for review
# Usage: scripts/review-pr.sh <pr-number>

set -euo pipefail

PR_NUMBER="${1:?Usage: scripts/review-pr.sh <pr-number>}"
REPO="maxthelion/octopoid"
OUT="/tmp/pr${PR_NUMBER}-diff.txt"

gh pr diff "$PR_NUMBER" --repo "$REPO" > "$OUT" 2>&1
LINES=$(wc -l < "$OUT")
echo "Saved to $OUT ($LINES lines)"
