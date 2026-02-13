#!/usr/bin/env python3
"""Requeue all claimed tasks back to incoming."""

import sys
sys.path.insert(0, ".")

from orchestrator.queue_utils import get_sdk


def main():
    sdk = get_sdk()
    claimed = sdk.tasks.list(queue="claimed")

    if not claimed:
        print("No claimed tasks to requeue.")
        return

    print(f"Requeuing {len(claimed)} claimed tasks...\n")

    for task in claimed:
        tid = task["id"]
        title = (task.get("title") or tid)[:60]
        try:
            sdk.tasks.update(tid, queue="incoming", claimed_by=None, claimed_at=None)
            print(f"  OK  {tid}  {title}")
        except Exception as e:
            print(f"  ERR {tid}  {title}  ({e})")

    print(f"\nDone.")


if __name__ == "__main__":
    main()
