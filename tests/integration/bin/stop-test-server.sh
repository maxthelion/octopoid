#!/bin/bash
# Stop test server

if [ -f /tmp/octopoid-test-server.pid ]; then
    PID=$(cat /tmp/octopoid-test-server.pid)
    echo "Stopping test server (PID $PID)..."
    kill $PID 2>/dev/null || true
    rm /tmp/octopoid-test-server.pid
    echo "✓ Test server stopped"
else
    echo "No test server PID file found"
fi

# Clean up log file
rm -f /tmp/octopoid-test-server.log
