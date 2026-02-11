"""
Octopoid SDK client implementation
"""

import os
from typing import Any, Dict, List, Optional
import requests

from .exceptions import (
    OctopoidAPIError,
    OctopoidNotFoundError,
    OctopoidAuthenticationError,
)


class TasksAPI:
    """Task operations"""

    def __init__(self, client: "OctopoidSDK"):
        self._client = client

    def create(
        self,
        id: str,
        file_path: str,
        queue: str = "incoming",
        priority: str = "P2",
        role: Optional[str] = None,
        branch: str = "main",
        project_id: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Create a new task

        Args:
            id: Task identifier
            file_path: Path to task file
            queue: Queue name (default: 'incoming')
            priority: Priority (P0-P3, default: 'P2')
            role: Task role (e.g., 'implement', 'test')
            branch: Git branch (default: 'main')
            project_id: Optional project ID
            **kwargs: Additional fields

        Returns:
            Task dict

        Example:
            >>> task = sdk.tasks.create(
            ...     id='my-task-123',
            ...     file_path='tasks/incoming/TASK-my-task-123.md',
            ...     priority='P1',
            ...     role='implement'
            ... )
        """
        data = {
            "id": id,
            "file_path": file_path,
            "queue": queue,
            "priority": priority,
            "branch": branch,
        }

        if role:
            data["role"] = role

        if project_id:
            data["project_id"] = project_id

        data.update(kwargs)

        return self._client._request("POST", "/api/v1/tasks", json=data)

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a task by ID

        Args:
            task_id: Task identifier

        Returns:
            Task dict or None if not found

        Example:
            >>> task = sdk.tasks.get('my-task-123')
            >>> if task:
            ...     print(f"Task {task['id']} is in {task['queue']} queue")
        """
        try:
            return self._client._request("GET", f"/api/v1/tasks/{task_id}")
        except OctopoidNotFoundError:
            return None

    def list(
        self,
        queue: Optional[str] = None,
        priority: Optional[str] = None,
        role: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        List tasks with optional filters

        Args:
            queue: Filter by queue
            priority: Filter by priority
            role: Filter by role
            limit: Maximum number of tasks
            offset: Pagination offset

        Returns:
            List of task dicts

        Example:
            >>> # List all incoming tasks
            >>> tasks = sdk.tasks.list(queue='incoming')
            >>>
            >>> # Filter by priority
            >>> p0_tasks = sdk.tasks.list(priority='P0')
        """
        params: Dict[str, Any] = {"limit": limit, "offset": offset}

        if queue:
            params["queue"] = queue
        if priority:
            params["priority"] = priority
        if role:
            params["role"] = role

        response = self._client._request("GET", "/api/v1/tasks", params=params)
        return response.get("tasks", [])

    def update(
        self, task_id: str, **updates: Any
    ) -> Dict[str, Any]:
        """
        Update a task

        Args:
            task_id: Task identifier
            **updates: Fields to update

        Returns:
            Updated task dict

        Example:
            >>> task = sdk.tasks.update(
            ...     'my-task-123',
            ...     priority='P0',
            ...     branch='feature/new-feature'
            ... )
        """
        return self._client._request(
            "PATCH", f"/api/v1/tasks/{task_id}", json=updates
        )

    def claim(
        self,
        orchestrator_id: str,
        agent_name: str,
        role_filter: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """
        Claim a task (for agents)

        Args:
            orchestrator_id: Orchestrator ID
            agent_name: Agent name
            role_filter: Optional role filter

        Returns:
            Claimed task dict or None if no tasks available
        """
        data = {
            "orchestrator_id": orchestrator_id,
            "agent_name": agent_name,
        }

        if role_filter:
            data["role_filter"] = role_filter

        try:
            return self._client._request("POST", "/api/v1/tasks/claim", json=data)
        except OctopoidNotFoundError:
            return None

    def submit(
        self,
        task_id: str,
        pr_url: Optional[str] = None,
        commits_count: int = 0,
        turns_used: int = 0,
    ) -> Dict[str, Any]:
        """
        Submit task completion (for agents)

        Args:
            task_id: Task identifier
            pr_url: Pull request URL
            commits_count: Number of commits
            turns_used: Number of AI turns used

        Returns:
            Updated task dict
        """
        data = {
            "pr_url": pr_url,
            "commits_count": commits_count,
            "turns_used": turns_used,
        }

        return self._client._request(
            "POST", f"/api/v1/tasks/{task_id}/submit", json=data
        )

    def accept(
        self, task_id: str, accepted_by: str = "manual-review"
    ) -> Dict[str, Any]:
        """
        Accept a completed task

        Args:
            task_id: Task identifier
            accepted_by: Who accepted the task

        Returns:
            Updated task dict

        Example:
            >>> sdk.tasks.accept('my-task-123', accepted_by='code-review')
        """
        data = {"accepted_by": accepted_by}

        return self._client._request(
            "POST", f"/api/v1/tasks/{task_id}/accept", json=data
        )

    def reject(
        self, task_id: str, reason: str, rejected_by: str = "manual-review"
    ) -> Dict[str, Any]:
        """
        Reject a completed task

        Args:
            task_id: Task identifier
            reason: Rejection reason
            rejected_by: Who rejected the task

        Returns:
            Updated task dict

        Example:
            >>> sdk.tasks.reject(
            ...     'my-task-123',
            ...     reason='Does not meet coding standards',
            ...     rejected_by='code-review'
            ... )
        """
        data = {"reason": reason, "rejected_by": rejected_by}

        return self._client._request(
            "POST", f"/api/v1/tasks/{task_id}/reject", json=data
        )


class ProjectsAPI:
    """Project operations"""

    def __init__(self, client: "OctopoidSDK"):
        self._client = client

    def create(
        self, id: str, name: str, description: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create a new project"""
        data = {"id": id, "name": name}

        if description:
            data["description"] = description

        return self._client._request("POST", "/api/v1/projects", json=data)

    def get(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Get a project by ID"""
        try:
            return self._client._request("GET", f"/api/v1/projects/{project_id}")
        except OctopoidNotFoundError:
            return None

    def list(self) -> List[Dict[str, Any]]:
        """List all projects"""
        response = self._client._request("GET", "/api/v1/projects")
        return response.get("projects", [])


class SystemAPI:
    """System operations"""

    def __init__(self, client: "OctopoidSDK"):
        self._client = client

    def health_check(self) -> Dict[str, Any]:
        """Check server health"""
        return self._client._request("GET", "/api/health")


class OctopoidSDK:
    """
    Main SDK class for interacting with Octopoid

    Args:
        server_url: Server URL (or OCTOPOID_SERVER_URL env var)
        api_key: API key (or OCTOPOID_API_KEY env var)
        timeout: Request timeout in seconds (default: 30)

    Example:
        >>> sdk = OctopoidSDK(
        ...     server_url='https://octopoid-server.username.workers.dev',
        ...     api_key='your-api-key'
        ... )
        >>>
        >>> # Create a task
        >>> task = sdk.tasks.create(
        ...     id='my-task',
        ...     file_path='tasks/incoming/TASK-my-task.md',
        ...     role='implement'
        ... )
    """

    def __init__(
        self,
        server_url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: int = 30,
    ):
        self.server_url = server_url or os.getenv("OCTOPOID_SERVER_URL")
        if not self.server_url:
            raise ValueError(
                "Server URL required. Set via parameter or OCTOPOID_SERVER_URL environment variable"
            )

        self.api_key = api_key or os.getenv("OCTOPOID_API_KEY")
        self.timeout = timeout

        # Initialize API namespaces
        self.tasks = TasksAPI(self)
        self.projects = ProjectsAPI(self)
        self.system = SystemAPI(self)

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """
        Make an API request

        Args:
            method: HTTP method
            path: API path
            params: Query parameters
            json: JSON body

        Returns:
            Response JSON

        Raises:
            OctopoidAPIError: If request fails
            OctopoidNotFoundError: If resource not found (404)
            OctopoidAuthenticationError: If authentication fails (401)
        """
        url = f"{self.server_url}{path}"

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        try:
            response = requests.request(
                method=method,
                url=url,
                params=params,
                json=json,
                headers=headers,
                timeout=self.timeout,
            )

            if response.status_code == 404:
                raise OctopoidNotFoundError(f"Resource not found: {path}")

            if response.status_code == 401:
                raise OctopoidAuthenticationError("Authentication failed")

            if response.status_code >= 400:
                raise OctopoidAPIError(
                    f"API request failed: {response.text}",
                    status_code=response.status_code,
                )

            # Handle empty responses (204 No Content)
            if response.status_code == 204:
                return None

            return response.json()

        except requests.exceptions.Timeout:
            raise OctopoidAPIError(f"Request timed out after {self.timeout} seconds")
        except requests.exceptions.ConnectionError as e:
            raise OctopoidAPIError(f"Connection error: {e}")
        except requests.exceptions.RequestException as e:
            raise OctopoidAPIError(f"Request failed: {e}")
