# Testing Guide

You are running tests or adding test coverage. Follow these guidelines:

## Understanding the Test Environment

1. **Identify the test framework** - look for pytest, jest, mocha, etc.
2. **Find existing tests** - understand the patterns used
3. **Check for test configuration** - look for config files

## Running Tests

### Common Commands
```bash
# Python/pytest
pytest
pytest -v  # verbose
pytest path/to/test_file.py  # specific file
pytest -k "test_name"  # specific test

# JavaScript/Node
npm test
npm run test:coverage

# Go
go test ./...
go test -v ./path/to/package
```

### Interpreting Results
- Read failure messages carefully
- Check for stack traces
- Note which tests pass vs fail

## Writing Tests

### Test Structure
```
Arrange - Set up test data and conditions
Act - Execute the code being tested
Assert - Verify the expected outcome
```

### Good Test Characteristics
- **Isolated** - tests don't depend on each other
- **Deterministic** - same result every time
- **Fast** - quick to run
- **Readable** - clear what's being tested

### What to Test
- Happy path (normal operation)
- Edge cases (empty inputs, boundaries)
- Error conditions (invalid inputs, failures)
- Integration points (API calls, database)

### Naming Conventions
```python
# Python
def test_function_name_condition_expected_result():
    pass

# Example
def test_calculate_total_with_discount_returns_reduced_price():
    pass
```

```javascript
// JavaScript
describe('function name', () => {
  it('should expected behavior when condition', () => {
    // test
  });
});
```

## Coverage

- Aim for meaningful coverage, not 100%
- Focus on critical paths and complex logic
- Don't test trivial getters/setters

## Remember

- You can only modify test files
- Do not change production code
- Document any bugs or issues found
- Report test results clearly
