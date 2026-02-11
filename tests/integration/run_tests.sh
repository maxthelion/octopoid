#!/bin/bash
# Run integration tests with test server

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

cd "$PROJECT_ROOT"

echo "=== Octopoid Integration Tests ==="
echo ""

# 1. Start test server
echo "1. Starting test server..."
"$SCRIPT_DIR/bin/start-test-server.sh"
echo ""

# Trap to ensure server stops on exit
trap '"$SCRIPT_DIR/bin/stop-test-server.sh"' EXIT

# 2. Run tests
echo "2. Running test suites..."
echo ""
pytest tests/integration/ -v --tb=short "$@"

echo ""
echo "=== Tests Complete ==="
