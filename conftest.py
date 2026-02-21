"""Root-level conftest.py — ensure worktree's local packages take precedence.

The Python SDK (octopoid_sdk) is installed in editable mode from the main
repo. This conftest ensures that when running tests from this worktree, the
worktree's copy of packages/python-sdk is loaded first, so changes made here
are picked up without reinstalling.
"""

import sys
from pathlib import Path

# Prepend this worktree's python-sdk to sys.path so it shadows the editable
# install from the main repo. We also remove any stale cached module so that
# reimport picks up the local version.
_worktree_sdk = str(Path(__file__).parent / "packages" / "python-sdk")
if _worktree_sdk not in sys.path:
    sys.path.insert(0, _worktree_sdk)

# Invalidate any already-cached import of octopoid_sdk so pytest's import
# machinery reloads it from the new path.
for _mod in list(sys.modules):
    if _mod == "octopoid_sdk" or _mod.startswith("octopoid_sdk."):
        del sys.modules[_mod]
