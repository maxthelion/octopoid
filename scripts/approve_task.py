#!/usr/bin/env python3
"""Approve a task: accept completion, merge PR, delete remote branch, unblock dependents.

This script previously approved tasks via the local SQLite database.
That functionality has been replaced by the API.
"""

import sys

print("This script is not available in API mode. Use the API or SDK instead.")
sys.exit(1)
