"""
Octopoid SDK Client
Main API client for interacting with Octopoid v2.0 server
"""

import requests
from typing import Optional, Dict, List, Any


class TasksAPI:
    """Tasks API endpoints"""

    def __init__(self, client: 'OctopoidSDK'):
        self.client = client

    def list(self, queue: Optional[str] = None, **filters) -> List[Dict[str, Any]]:
        """List tasks with optional filters"""
        params = {}
        if queue:
            params['queue'] = queue
        params.update(filters)

        response = self.client._request('GET', '/api/v1/tasks', params=params)
        if isinstance(response, dict) and 'tasks' in response:
            return response['tasks']
        return response if isinstance(response, list) else []

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get a single task by ID"""
        try:
            return self.client._request('GET', f'/api/v1/tasks/{task_id}')
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                return None
            raise

    def create(
        self,
        id: str,
        file_path: str,
        title: Optional[str] = None,
        role: Optional[str] = None,
        priority: Optional[str] = None,
        context: Optional[str] = None,
        acceptance_criteria: Optional[str] = None,
        queue: str = 'incoming',
        branch: str = 'main',
        metadata: Optional[Dict[str, Any]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        """Create a new task

        Args:
            id: Task ID (e.g., 'task-001')
            file_path: Path to task markdown file
            title: Task title
            role: Agent role (e.g., 'implement', 'test')
            priority: Priority level (P0, P1, P2)
            context: Task context/description
            acceptance_criteria: Success criteria
            queue: Initial queue (default: 'incoming')
            branch: Base branch (default: 'main')
            metadata: Additional metadata
            **kwargs: Additional task fields

        Returns:
            Created task dictionary
        """
        data = {
            'id': id,
            'file_path': file_path,
            'queue': queue,
            'branch': branch,
        }

        if title:
            data['title'] = title
        if role:
            data['role'] = role
        if priority:
            data['priority'] = priority
        if metadata:
            data['metadata'] = metadata

        # Add any additional fields
        data.update(kwargs)

        return self.client._request('POST', '/api/v1/tasks', json=data)

    def claim(
        self,
        orchestrator_id: str,
        agent_name: str,
        role_filter: Optional[str] = None,
        type_filter: Optional[str] = None,
        lease_duration_seconds: Optional[int] = None,
        max_claimed: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """Claim an available task

        Args:
            orchestrator_id: Orchestrator identifier
            agent_name: Agent name
            role_filter: Filter by role (e.g., 'implement')
            type_filter: Filter by task type (e.g., 'product')
            lease_duration_seconds: Lease duration in seconds
            max_claimed: Max claimed tasks for this orchestrator (server enforced)

        Returns:
            Claimed task dictionary, or None if no tasks available
        """
        data = {
            'orchestrator_id': orchestrator_id,
            'agent_name': agent_name,
        }

        if role_filter:
            data['role_filter'] = role_filter
        if type_filter:
            data['type_filter'] = type_filter
        if lease_duration_seconds:
            data['lease_duration_seconds'] = lease_duration_seconds
        if max_claimed is not None:
            data['max_claimed'] = max_claimed

        try:
            return self.client._request('POST', '/api/v1/tasks/claim', json=data)
        except requests.HTTPError as e:
            if e.response.status_code in (404, 429):
                return None
            raise

    def submit(
        self,
        task_id: str,
        commits_count: int,
        turns_used: int,
        check_results: Optional[str] = None,
        execution_notes: Optional[str] = None
    ) -> Dict[str, Any]:
        """Submit task completion

        Args:
            task_id: Task ID
            commits_count: Number of commits made
            turns_used: Number of turns/iterations used
            check_results: Optional check results
            execution_notes: Optional execution summary (high-level overview)

        Returns:
            Updated task dictionary
        """
        data = {
            'commits_count': commits_count,
            'turns_used': turns_used,
        }

        if check_results:
            data['check_results'] = check_results
        if execution_notes:
            data['execution_notes'] = execution_notes

        return self.client._request('POST', f'/api/v1/tasks/{task_id}/submit', json=data)

    def delete(self, task_id: str) -> Dict[str, Any]:
        """Delete a task

        Args:
            task_id: Task ID

        Returns:
            Deletion confirmation
        """
        return self.client._request('DELETE', f'/api/v1/tasks/{task_id}')

    def update(self, task_id: str, **updates) -> Dict[str, Any]:
        """Update task fields

        Args:
            task_id: Task ID
            **updates: Fields to update (queue, priority, title, etc.)

        Returns:
            Updated task dictionary
        """
        return self.client._request('PATCH', f'/api/v1/tasks/{task_id}', json=updates)

    def accept(self, task_id: str, accepted_by: Optional[str] = None) -> Dict[str, Any]:
        """Accept a completed task

        Args:
            task_id: Task ID
            accepted_by: Name of accepter (e.g., "gatekeeper", "human")

        Returns:
            Updated task dictionary
        """
        data = {}
        if accepted_by:
            data['accepted_by'] = accepted_by

        return self.client._request('POST', f'/api/v1/tasks/{task_id}/accept', json=data)

    def reject(self, task_id: str, reason: str, rejected_by: Optional[str] = None) -> Dict[str, Any]:
        """Reject a completed task

        Args:
            task_id: Task ID
            reason: Rejection reason/feedback
            rejected_by: Name of rejecter (e.g., "gatekeeper", "human")

        Returns:
            Updated task dictionary
        """
        data = {'reason': reason}
        if rejected_by:
            data['rejected_by'] = rejected_by

        return self.client._request('POST', f'/api/v1/tasks/{task_id}/reject', json=data)

    def requeue(self, task_id: str) -> Dict[str, Any]:
        """Requeue a claimed task back to incoming

        Args:
            task_id: Task ID

        Returns:
            Updated task dictionary
        """
        return self.client._request('POST', f'/api/v1/tasks/{task_id}/requeue', json={})


class DraftsAPI:
    """Drafts API endpoints"""

    def __init__(self, client: 'OctopoidSDK'):
        self.client = client

    def list(self, status: Optional[str] = None, **filters) -> List[Dict[str, Any]]:
        """List drafts with optional filters"""
        params = {}
        if status:
            params['status'] = status
        params.update(filters)

        response = self.client._request('GET', '/api/v1/drafts', params=params)
        if isinstance(response, dict) and 'drafts' in response:
            return response['drafts']
        return response if isinstance(response, list) else []

    def create(
        self,
        title: str,
        author: str,
        file_path: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new draft. Server auto-assigns an integer ID.

        Args:
            title: Draft title
            author: Author name (e.g., 'human')
            file_path: Path to draft markdown file
            status: Draft status (default: 'idea')

        Returns:
            Created draft dictionary including server-assigned 'id'
        """
        data: Dict[str, Any] = {
            'title': title,
            'author': author,
        }

        if file_path:
            data['file_path'] = file_path
        if status:
            data['status'] = status

        return self.client._request('POST', '/api/v1/drafts', json=data)


class ProjectsAPI:
    """Projects API endpoints"""

    def __init__(self, client: 'OctopoidSDK'):
        self.client = client

    def list(self, status: Optional[str] = None, **filters) -> List[Dict[str, Any]]:
        """List projects with optional filters"""
        params = {}
        if status:
            params['status'] = status
        params.update(filters)

        response = self.client._request('GET', '/api/v1/projects', params=params)
        if isinstance(response, dict) and 'projects' in response:
            return response['projects']
        return response if isinstance(response, list) else []


class StatusAPI:
    """Status and health API endpoints"""

    def __init__(self, client: 'OctopoidSDK'):
        self.client = client

    def health(self) -> Dict[str, Any]:
        """Get server health status"""
        return self.client._request('GET', '/api/health')


class DebugAPI:
    """Debug and observability API endpoints"""

    def __init__(self, client: 'OctopoidSDK'):
        self.client = client

    def task(self, task_id: str) -> Dict[str, Any]:
        """Get debug information for a specific task

        Args:
            task_id: Task ID

        Returns:
            Task debug information including lease status, blocking info,
            burnout metrics, and gatekeeper stats
        """
        return self.client._request('GET', f'/api/v1/tasks/{task_id}/debug')

    def queues(self) -> Dict[str, Any]:
        """Get debug information for all queues

        Returns:
            Queue stats including task counts, oldest tasks, and claimed task details
        """
        return self.client._request('GET', '/api/v1/debug/queues')

    def agents(self) -> Dict[str, Any]:
        """Get debug information for all agents and orchestrators

        Returns:
            Agent activity, orchestrator health, and summary statistics
        """
        return self.client._request('GET', '/api/v1/debug/agents')

    def status(self) -> Dict[str, Any]:
        """Get comprehensive system status overview

        Returns:
            Complete system status including queues, agents, health metrics,
            and performance statistics
        """
        return self.client._request('GET', '/api/v1/debug/status')


class OctopoidSDK:
    """
    Main Octopoid SDK client

    Usage:
        sdk = OctopoidSDK(
            server_url='https://octopoid-server.username.workers.dev',
            api_key='your-api-key'
        )

        # List tasks
        tasks = sdk.tasks.list(queue='incoming')
    """

    def __init__(
        self,
        server_url: str,
        api_key: Optional[str] = None,
        timeout: int = 30
    ):
        self.server_url = server_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        self.session = requests.Session()

        if api_key:
            self.session.headers['Authorization'] = f'Bearer {api_key}'

        # Initialize API endpoints
        self.tasks = TasksAPI(self)
        self.drafts = DraftsAPI(self)
        self.projects = ProjectsAPI(self)
        self.status = StatusAPI(self)
        self.debug = DebugAPI(self)

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict] = None,
        json: Optional[Dict] = None
    ) -> Any:
        """Make HTTP request to API"""
        url = f'{self.server_url}{path}'

        try:
            response = self.session.request(
                method,
                url,
                params=params,
                json=json,
                timeout=self.timeout
            )
            response.raise_for_status()

            if response.status_code == 204:
                return None

            try:
                return response.json()
            except ValueError:
                return response.text

        except requests.Timeout:
            raise TimeoutError(f'Request to {url} timed out after {self.timeout}s')

    def close(self):
        """Close the session"""
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
