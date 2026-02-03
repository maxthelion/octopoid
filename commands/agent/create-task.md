# Create Task

Create a well-formed task for the orchestrator queue.

## Task Format

Tasks are markdown files with specific metadata fields:

```markdown
# [TASK-{uuid8}] {Title}

ROLE: implement | test | review
PRIORITY: P0 | P1 | P2
BRANCH: {base_branch}
CREATED: {ISO8601_timestamp}
CREATED_BY: {agent_name}

## Context
{Background and motivation}

## Acceptance Criteria
- [ ] {Specific, measurable criterion}
- [ ] {Another criterion}
```

## Fields

### ROLE
- `implement` - Code changes, new features, bug fixes
- `test` - Test writing, test running, coverage improvements
- `review` - Code review, security audit

### PRIORITY
- `P0` - Critical/urgent (security issues, broken builds)
- `P1` - High priority (important features, significant bugs)
- `P2` - Normal priority (improvements, minor issues)

### BRANCH
The base branch to work from:
- `main` - Most tasks
- `feature/xyz` - Tasks that build on in-progress features
- `hotfix/xyz` - Urgent fixes

## Writing Good Tasks

### Title
- Be specific: "Add rate limiting to /api/auth endpoints"
- Not vague: "Improve security"

### Context
- Explain WHY this task matters
- Provide background for someone unfamiliar
- Link to related issues/PRs if relevant

### Acceptance Criteria
- Specific and measurable
- Each criterion independently verifiable
- Include edge cases that must be handled

## Example Task

```markdown
# [TASK-a1b2c3d4] Add input validation to user registration

ROLE: implement
PRIORITY: P1
BRANCH: main
CREATED: 2024-01-15T10:30:00Z
CREATED_BY: pm-agent

## Context
The user registration endpoint currently accepts any input without validation.
This could lead to invalid data in the database and potential security issues.
Related: Issue #42

## Acceptance Criteria
- [ ] Email addresses are validated for proper format
- [ ] Passwords require minimum 8 characters, 1 number, 1 special char
- [ ] Username is alphanumeric, 3-20 characters
- [ ] Validation errors return 400 with specific error messages
- [ ] Unit tests cover all validation rules
```

## Creating the Task

### Step 1: Generate a unique ID

```bash
TASK_ID="TASK-$(openssl rand -hex 4)"
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
echo "Creating ${TASK_ID}"
```

### Step 2: Write the task file

```bash
mkdir -p .orchestrator/shared/queue/incoming

cat > ".orchestrator/shared/queue/incoming/${TASK_ID}.md" << EOF
# [${TASK_ID}] Add input validation to user registration

ROLE: implement
PRIORITY: P1
BRANCH: main
CREATED: ${TIMESTAMP}
CREATED_BY: ${AGENT_NAME}

## Context

The user registration endpoint currently accepts any input without validation.
This could lead to invalid data in the database and potential security issues.
Related: Issue #42

## Acceptance Criteria
- [ ] Email addresses are validated for proper format
- [ ] Passwords require minimum 8 characters, 1 number, 1 special char
- [ ] Username is alphanumeric, 3-20 characters
- [ ] Validation errors return 400 with specific error messages
- [ ] Unit tests cover all validation rules
EOF
```

## After Creation

The task will be:
1. Placed in `.orchestrator/shared/queue/incoming/`
2. Available for claiming by agents with matching roles
3. Moved to `claimed/` when an agent picks it up
4. Moved to `done/` or `failed/` when complete
