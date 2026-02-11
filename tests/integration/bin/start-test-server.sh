#!/bin/bash
# Start test server on port 9787 with test database

set -e

cd "$(dirname "$0")/../../../packages/server"

echo "Starting test server..."

# Kill any existing test server
if [ -f /tmp/octopoid-test-server.pid ]; then
    OLD_PID=$(cat /tmp/octopoid-test-server.pid)
    if kill -0 $OLD_PID 2>/dev/null; then
        echo "Stopping existing test server (PID $OLD_PID)..."
        kill $OLD_PID || true
        sleep 1
    fi
    rm /tmp/octopoid-test-server.pid
fi

# Start wrangler dev in background
npx wrangler dev --config wrangler.test.toml > /tmp/octopoid-test-server.log 2>&1 &
echo $! > /tmp/octopoid-test-server.pid

echo "Waiting for server to be ready..."
for i in {1..30}; do
    if curl -s http://localhost:9787/api/health > /dev/null 2>&1; then
        echo "✓ Test server ready on port 9787"

        # Apply migrations to local D1 database using wrangler
        echo "Applying migrations..."

        for migration in migrations/*.sql; do
            echo "  - Applying $(basename $migration)..."
            npx wrangler d1 execute octopoid-test --local --config wrangler.test.toml --file="$migration" 2>&1 | grep -v "^$" | head -3
        done

        echo "✓ Migrations applied"

        exit 0
    fi
    sleep 1
    echo -n "."
done

echo ""
echo "✗ Test server failed to start. Check /tmp/octopoid-test-server.log"
cat /tmp/octopoid-test-server.log
exit 1
