# Implementation Guide

You are implementing a feature or fix. Follow these guidelines:

## Before You Start

1. **Read the task thoroughly** - understand all acceptance criteria
2. **Explore the codebase** - find related code, understand patterns
3. **Plan your approach** - think before coding

## Implementation Standards

### Code Quality
- Follow existing code patterns and conventions
- Keep functions/methods focused and small
- Use meaningful names for variables and functions
- Add comments only where logic isn't self-evident

### Error Handling
- Handle errors appropriately for the context
- Don't swallow errors silently
- Provide helpful error messages

### Security
- Never hardcode secrets or credentials
- Validate and sanitize inputs at boundaries
- Be aware of OWASP Top 10 vulnerabilities

## Git Workflow

### Commits
- Make atomic commits (one logical change per commit)
- Write clear commit messages:
  ```
  type: brief description

  More details if needed.
  ```
- Types: feat, fix, refactor, test, docs, chore

### Commit Message Examples
- `feat: add user authentication endpoint`
- `fix: handle null pointer in payment processor`
- `refactor: extract validation logic to helper`
- `test: add unit tests for order service`

## Testing

- Write tests for new functionality
- Ensure existing tests still pass
- Test edge cases and error conditions
- Run the test suite before considering work complete

## When You're Done

1. Review your changes (`git diff`)
2. Ensure all tests pass
3. Make final commit if needed
4. Summarize what you implemented
