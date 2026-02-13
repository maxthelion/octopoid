#!/usr/bin/env python3
"""Cancel a task: remove from DB and archive file to cancelled/.

This script previously cancelled tasks via the local SQLite database.
That functionality has been replaced by the API.
"""

import sys

print("This script is not available in API mode. Use the API or SDK instead.")
sys.exit(1)
