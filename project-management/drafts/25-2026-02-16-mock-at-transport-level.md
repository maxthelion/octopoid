# Mock SDK at Transport Level, Not Per-Module Patches

**Status:** Idea
**Captured:** 2026-02-16

## Problem

After the queue_utils entity module split, the test `conftest.py` patches `get_sdk` at 4 separate module paths:

```python
with patch('orchestrator.sdk.get_sdk', return_value=mock_sdk):
    with patch('orchestrator.tasks.get_sdk', return_value=mock_sdk):
        with patch('orchestrator.projects.get_sdk', return_value=mock_sdk):
            with patch('orchestrator.breakdowns.get_sdk', return_value=mock_sdk):
                yield mock_sdk
```

This is fragile. Every new module that imports `get_sdk` needs a new patch line. We've already been bitten by this — the first refactor attempt broke mocks and created 45+ junk tasks on production because `orchestrator.tasks` imported `get_sdk` from `orchestrator.sdk` but tests only patched `orchestrator.queue_utils.get_sdk`.

The pattern of patching at every import site doesn't scale and will break again the next time imports are reorganised.

## Options

### Option A: Mock at the SDK singleton level
Patch `orchestrator.sdk._sdk` (the module-level cached instance) instead of `get_sdk`. Since `get_sdk()` returns `_sdk` if it's already set, patching the singleton means all callers get the mock regardless of which module they're in.

```python
@pytest.fixture(autouse=True)
def mock_sdk(mock_sdk_instance):
    import orchestrator.sdk as sdk_module
    original = sdk_module._sdk
    sdk_module._sdk = mock_sdk_instance
    yield mock_sdk_instance
    sdk_module._sdk = original
```

Pro: One patch, survives import restructuring.
Con: Relies on internal `_sdk` variable name.

### Option B: Mock the HTTP transport (requests.Session)
Patch `requests.Session.request` or use `responses` / `respx` library. All SDK calls go through HTTP — mock at that layer and nothing above it matters.

```python
@pytest.fixture(autouse=True)
def mock_http(responses):
    responses.add(responses.GET, re.compile(r'.*/api/v1/tasks.*'), json=[])
    responses.add(responses.POST, re.compile(r'.*/api/v1/tasks.*'), json={'id': 'TASK-test'})
    yield
```

Pro: Completely decoupled from import structure. Tests verify real SDK serialisation.
Con: More verbose setup. Need to define responses for each endpoint used.

### Option C: SDK test mode
Add `OctopoidSDK(test_mode=True)` that returns canned responses without making HTTP calls.

Pro: Clean API, easy to use.
Con: Requires SDK code changes. Mock responses may drift from real server.

### Option D: Patch `get_sdk` at its canonical definition only
Since all modules do `from .sdk import get_sdk`, patching `orchestrator.sdk.get_sdk` should be enough — Python's import system means all modules get the same function object.

Wait — that's what we thought before and it didn't work. The issue is that `from .sdk import get_sdk` creates a new name binding in each module. Patching the original doesn't affect the copies.

This confirms Options A or B are the right approach.

## Recommendation

**Option A (singleton patch)** for short-term — minimal change, one line replaces 4 patches.

**Option B (HTTP transport mock)** for long-term — most robust, also catches SDK bugs. Could use the `responses` library which is already commonly used in Python test suites.

## Scope

- Update `tests/conftest.py` to use singleton or transport-level mock
- Remove all per-module `get_sdk` patches
- Verify no tests create real server tasks (count incoming before/after)
