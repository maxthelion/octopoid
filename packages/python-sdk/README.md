# Octopoid SDK for Python

Write custom scripts and automation for your Octopoid orchestrator in Python.

## Installation

```bash
pip install octopoid-sdk
```

## Quick Start

```python
from octopoid_sdk import OctopoidSDK

# Initialize SDK
sdk = OctopoidSDK(
    server_url='https://octopoid-server.username.workers.dev',
    api_key='your-api-key'  # or set OCTOPOID_API_KEY env var
)

# Create a task
task = sdk.tasks.create(
    id='implement-feature-x',
    file_path='tasks/incoming/TASK-implement-feature-x.md',
    queue='incoming',
    priority='P1',
    role='implement'
)

print(f"Created task: {task['id']}")
```

## Configuration

The SDK can be configured via constructor parameters or environment variables:

```python
# Via constructor
sdk = OctopoidSDK(
    server_url='https://octopoid-server.username.workers.dev',
    api_key='your-api-key',
    timeout=30  # Request timeout in seconds
)

# Via environment variables
import os
os.environ['OCTOPOID_SERVER_URL'] = 'https://...'
os.environ['OCTOPOID_API_KEY'] = 'your-api-key'

sdk = OctopoidSDK()  # Will use env vars
```

## API Reference

### Tasks

#### Create a Task

```python
task = sdk.tasks.create(
    id='my-task-123',
    file_path='tasks/incoming/TASK-my-task-123.md',
    queue='incoming',
    priority='P0',  # P0 (highest) to P3 (lowest)
    role='implement',
    branch='main',
    project_id='my-project'
)
```

#### Get a Task

```python
task = sdk.tasks.get('my-task-123')

if task:
    print(f"Task {task['id']} is in {task['queue']} queue")
else:
    print("Task not found")
```

#### List Tasks

```python
# List all incoming tasks
tasks = sdk.tasks.list(queue='incoming')

# Filter by priority
p0_tasks = sdk.tasks.list(priority='P0')

# Filter by role
implement_tasks = sdk.tasks.list(role='implement')

# Limit results
recent_tasks = sdk.tasks.list(limit=10)
```

#### Update a Task

```python
task = sdk.tasks.update(
    'my-task-123',
    priority='P0',
    branch='feature/new-feature'
)
```

#### Accept a Task (Manual Approval)

```python
sdk.tasks.accept(
    'my-task-123',
    accepted_by='manual-review'
)
```

#### Reject a Task

```python
sdk.tasks.reject(
    'my-task-123',
    reason='Does not meet coding standards',
    rejected_by='code-review'
)
```

### Projects

#### Create a Project

```python
project = sdk.projects.create(
    id='web-app',
    name='Web Application',
    description='Main web application'
)
```

#### Get a Project

```python
project = sdk.projects.get('web-app')
```

#### List Projects

```python
projects = sdk.projects.list()
```

### System

#### Health Check

```python
health = sdk.system.health_check()
print(f"Server status: {health['status']}")
```

## Example Scripts

### Auto-Approve Low-Risk Tasks

```python
#!/usr/bin/env python3
"""Auto-approve documentation and test tasks"""

from octopoid_sdk import OctopoidSDK

sdk = OctopoidSDK()

# Find provisional tasks
tasks = sdk.tasks.list(queue='provisional')

for task in tasks:
    # Auto-approve docs and test tasks with commits
    if task['role'] in ('docs', 'test') and task.get('commits_count', 0) > 0:
        print(f"✅ Auto-approving {task['role']} task: {task['id']}")
        sdk.tasks.accept(task['id'], accepted_by='auto-approve-script')
    else:
        print(f"⏭️  Skipping {task.get('role', 'unknown')} task: {task['id']}")
```

### Create Tasks from JSON File

```python
#!/usr/bin/env python3
"""Create tasks from a JSON file"""

import json
from octopoid_sdk import OctopoidSDK

sdk = OctopoidSDK()

# Read feature requests from JSON
with open('features.json') as f:
    features = json.load(f)

for feature in features:
    task_id = f"feature-{feature['id']}"

    sdk.tasks.create(
        id=task_id,
        file_path=f"tasks/incoming/TASK-{task_id}.md",
        queue='incoming',
        priority=feature['priority'],
        role='implement',
        project_id=feature.get('project')
    )

    print(f"Created task for: {feature['title']}")
```

### Monitor Task Progress

```python
#!/usr/bin/env python3
"""Monitor a task until completion"""

import time
from octopoid_sdk import OctopoidSDK

sdk = OctopoidSDK()

task_id = 'my-task-123'

while True:
    task = sdk.tasks.get(task_id)

    if not task:
        print("Task not found")
        break

    print(f"Task {task['id']}: {task['queue']}")

    if task['queue'] == 'done':
        print("✅ Task completed!")
        break

    if task['queue'] == 'provisional':
        print("🔍 Task awaiting review")
        break

    # Wait 10 seconds before next check
    time.sleep(10)
```

### Generate Daily Report

```python
#!/usr/bin/env python3
"""Generate a daily task report"""

from octopoid_sdk import OctopoidSDK
from datetime import datetime

sdk = OctopoidSDK()

# Get task counts by queue
queues = ['incoming', 'claimed', 'provisional', 'done']

print("📊 Daily Report\n")

for queue in queues:
    tasks = sdk.tasks.list(queue=queue, limit=1000)

    print(f"{queue.upper()}: {len(tasks)}")

    if tasks:
        # Show first 3 tasks
        for task in tasks[:3]:
            role = task.get('role') or 'no role'
            print(f"  • {task['id']} ({task['priority']}) - {role}")

        if len(tasks) > 3:
            print(f"  ... and {len(tasks) - 3} more")

    print()

# List today's completed tasks
done_tasks = sdk.tasks.list(queue='done', limit=1000)
today = datetime.now().date().isoformat()

todays_done = [
    t for t in done_tasks
    if t.get('completed_at') and t['completed_at'].startswith(today)
]

print(f"✅ Completed Today: {len(todays_done)}")
for task in todays_done:
    print(f"  • {task['id']} ({task.get('role', 'unknown')})")
```

### Batch Operations

```python
#!/usr/bin/env python3
"""Batch update task priorities"""

from octopoid_sdk import OctopoidSDK

sdk = OctopoidSDK()

# Get all P2 tasks in incoming queue
tasks = sdk.tasks.list(queue='incoming', priority='P2')

print(f"Found {len(tasks)} P2 tasks")

# Upgrade certain tasks to P1
for task in tasks:
    if task.get('role') == 'bug-fix':
        sdk.tasks.update(task['id'], priority='P1')
        print(f"✅ Upgraded {task['id']} to P1")
```

## Error Handling

```python
from octopoid_sdk import (
    OctopoidSDK,
    OctopoidError,
    OctopoidAPIError,
    OctopoidNotFoundError,
    OctopoidAuthenticationError
)

sdk = OctopoidSDK()

try:
    task = sdk.tasks.get('non-existent-task')
    if task:
        print(f"Found task: {task['id']}")
    else:
        print("Task not found")

except OctopoidNotFoundError as e:
    print(f"Not found: {e}")

except OctopoidAuthenticationError as e:
    print(f"Authentication failed: {e}")

except OctopoidAPIError as e:
    print(f"API error (status {e.status_code}): {e}")

except OctopoidError as e:
    print(f"SDK error: {e}")
```

## Type Hints

The SDK includes type hints for better IDE support:

```python
from typing import Dict, List, Optional
from octopoid_sdk import OctopoidSDK

sdk = OctopoidSDK()

# Type annotations help catch errors
task: Optional[Dict[str, Any]] = sdk.tasks.get('my-task')
tasks: List[Dict[str, Any]] = sdk.tasks.list(queue='incoming')
```

## Command-Line Usage

You can also use the SDK in command-line scripts:

```bash
# create_task.py
#!/usr/bin/env python3
import sys
from octopoid_sdk import OctopoidSDK

sdk = OctopoidSDK()

task_id = sys.argv[1] if len(sys.argv) > 1 else 'default-task'

task = sdk.tasks.create(
    id=task_id,
    file_path=f'tasks/incoming/TASK-{task_id}.md',
    role='implement',
    priority='P2'
)

print(f"Created: {task['id']}")
```

```bash
chmod +x create_task.py
./create_task.py my-new-task
```

## Development

To contribute to the SDK:

```bash
# Clone repository
git clone https://github.com/org/octopoid.git
cd octopoid/packages/python-sdk

# Install in development mode
pip install -e .[dev]

# Run tests
pytest

# Format code
black octopoid_sdk/

# Type check
mypy octopoid_sdk/
```

## License

MIT

## Contributing

Contributions are welcome! Please see the main [Octopoid repository](https://github.com/org/octopoid) for contribution guidelines.
