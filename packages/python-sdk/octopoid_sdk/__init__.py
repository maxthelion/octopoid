"""
Octopoid SDK for Python

Write custom scripts and automation for your Octopoid orchestrator.

Example:
    >>> from octopoid_sdk import OctopoidSDK
    >>>
    >>> sdk = OctopoidSDK(
    ...     server_url='https://octopoid-server.username.workers.dev',
    ...     api_key='your-api-key'
    ... )
    >>>
    >>> # Create a task
    >>> task = sdk.tasks.create(
    ...     id='my-task-123',
    ...     file_path='tasks/incoming/TASK-my-task-123.md',
    ...     queue='incoming',
    ...     priority='P1',
    ...     role='implement'
    ... )
    >>>
    >>> # List tasks
    >>> tasks = sdk.tasks.list(queue='incoming')
    >>>
    >>> # Get a task
    >>> task = sdk.tasks.get('my-task-123')
"""

from .client import OctopoidSDK
from .exceptions import (
    OctopoidError,
    OctopoidAPIError,
    OctopoidNotFoundError,
    OctopoidAuthenticationError,
)

__version__ = "2.0.0"
__all__ = [
    "OctopoidSDK",
    "OctopoidError",
    "OctopoidAPIError",
    "OctopoidNotFoundError",
    "OctopoidAuthenticationError",
]
