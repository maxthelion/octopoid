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
        queue: str = 'incoming',
        branch: Optional[str] = None,
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
            queue: Initial queue (default: 'incoming')
            branch: Base branch (default: None, server/scheduler resolves)
            metadata: Additional metadata
            **kwargs: Additional task fields

        Returns:
            Created task dictionary

        Note:
            Task content (context, acceptance criteria, etc.) should be written
            to the file_path. The local file is the canonical source of truth.
        """
        data = {
            'id': id,
            'file_path': file_path,
            'queue': queue,
        }
        if branch is not None:
            data['branch'] = branch

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
        max_claimed: Optional[int] = None,
        queue: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Claim an available task

        Args:
            orchestrator_id: Orchestrator identifier
            agent_name: Agent name
            role_filter: Filter by role (e.g., 'implement')
            type_filter: Filter by task type (e.g., 'product')
            lease_duration_seconds: Lease duration in seconds
            max_claimed: Max claimed tasks for this orchestrator (server enforced)
            queue: Queue to claim from (default: 'incoming'). Use 'provisional'
                   for gatekeeper review claims.

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
        if queue is not None:
            data['queue'] = queue

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

    def get(self, project_id: str) -> Optional[Dict[str, Any]]:
        """Get a single project by ID"""
        try:
            return self.client._request('GET', f'/api/v1/projects/{project_id}')
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                return None
            raise

    def create(self, **fields) -> Dict[str, Any]:
        """Create a new project"""
        return self.client._request('POST', '/api/v1/projects', json=fields)

    def update(self, project_id: str, **updates) -> Dict[str, Any]:
        """Update project fields"""
        return self.client._request('PATCH', f'/api/v1/projects/{project_id}', json=updates)

    def get_tasks(self, project_id: str) -> List[Dict[str, Any]]:
        """Get all tasks belonging to a project"""
        response = self.client._request('GET', f'/api/v1/projects/{project_id}/tasks')
        if isinstance(response, dict) and 'tasks' in response:
            return response['tasks']
        return response if isinstance(response, list) else []


class FlowsAPI:
    """Flows API endpoints — register flow definitions with the server"""

    def __init__(self, client: 'OctopoidSDK'):
        self.client = client

    def register(
        self,
        name: str,
        states: List[str],
        transitions: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Register (upsert) a flow definition on the server.

        Args:
            name: Flow name (e.g., 'default')
            states: List of all queue names used by this flow
            transitions: Optional list of transition dicts with 'from' and 'to' keys

        Returns:
            Server response dict
        """
        data: Dict[str, Any] = {'states': states}
        if transitions is not None:
            data['transitions'] = transitions
        return self.client._request('PUT', f'/api/v1/flows/{name}', json=data)


class MessagesAPI:
    """Messages API endpoints"""

    def __init__(self, client: 'OctopoidSDK'):
        self._client = client

    def create(
        self,
        task_id: str,
        from_actor: str,
        type: str,
        content: str,
        to_actor: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new message

        Args:
            task_id: Task ID this message belongs to
            from_actor: Actor sending the message (e.g., 'agent', 'human')
            type: Message type (e.g., 'comment', 'question')
            content: Message content
            to_actor: Optional recipient actor

        Returns:
            Created message dictionary
        """
        payload: Dict[str, Any] = {
            'task_id': task_id,
            'from_actor': from_actor,
            'type': type,
            'content': content,
        }
        if to_actor:
            payload['to_actor'] = to_actor
        return self._client._request('POST', '/api/v1/messages', json=payload)

    def list(
        self,
        task_id: Optional[str] = None,
        to_actor: Optional[str] = None,
        type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List messages with optional filters

        Args:
            task_id: Filter by task ID
            to_actor: Filter by recipient actor
            type: Filter by message type

        Returns:
            List of message dictionaries
        """
        params: Dict[str, Any] = {}
        if task_id:
            params['task_id'] = task_id
        if to_actor:
            params['to_actor'] = to_actor
        if type:
            params['type'] = type
        return self._client._request('GET', '/api/v1/messages', params=params).get('messages', [])


class ActionsAPI:
    """Actions API endpoints"""

    def __init__(self, client: 'OctopoidSDK'):
        self.client = client

    def create(
        self,
        entity_type: str,
        entity_id: str,
        label: str,
        description: Optional[str] = None,
        action_data: Optional[Dict[str, Any]] = None,
        action_type: str = "proposal",
        proposed_by: Optional[str] = None,
        expires_at: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create a new action.

        Args:
            entity_type: Type of entity (e.g., 'task', 'draft')
            entity_id: ID of the entity
            label: Human-readable label for the action
            description: Optional description of the action
            action_data: Optional dict containing button definitions, e.g.
                         {"buttons": [{"label": "Approve", "command": "approve"}]}
            action_type: Action type (default: 'proposal')
            proposed_by: Optional actor proposing the action
            expires_at: Optional ISO8601 expiry timestamp

        Returns:
            Created action dictionary
        """
        data: Dict[str, Any] = {
            'entity_type': entity_type,
            'entity_id': entity_id,
            'label': label,
            'action_type': action_type,
        }
        if description is not None:
            data['description'] = description
        if action_data is not None:
            data['action_data'] = action_data
        if proposed_by is not None:
            data['proposed_by'] = proposed_by
        if expires_at is not None:
            data['expires_at'] = expires_at
        return self.client._request('POST', '/api/v1/actions', json=data)

    def list(
        self,
        entity_type: Optional[str] = None,
        entity_id: Optional[str] = None,
        status: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """List actions with optional filters.

        Args:
            entity_type: Filter by entity type
            entity_id: Filter by entity ID
            status: Filter by status (e.g., 'pending', 'execute_requested', 'completed', 'failed')

        Returns:
            List of action dictionaries
        """
        params: Dict[str, Any] = {}
        if entity_type is not None:
            params['entity_type'] = entity_type
        if entity_id is not None:
            params['entity_id'] = entity_id
        if status is not None:
            params['status'] = status
        response = self.client._request('GET', '/api/v1/actions', params=params)
        if isinstance(response, dict) and 'actions' in response:
            return response['actions']
        return response if isinstance(response, list) else []

    def execute(self, action_id: str) -> Dict[str, Any]:
        """Request execution of an action (sets status to execute_requested).

        Args:
            action_id: Action ID

        Returns:
            Updated action dictionary
        """
        return self.client._request('POST', f'/api/v1/actions/{action_id}/execute', json={})

    def complete(self, action_id: str, result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Mark an action as completed.

        Args:
            action_id: Action ID
            result: Optional result dict to store with the action

        Returns:
            Updated action dictionary
        """
        data: Dict[str, Any] = {}
        if result is not None:
            data['result'] = result
        return self.client._request('POST', f'/api/v1/actions/{action_id}/complete', json=data)

    def fail(self, action_id: str, result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Mark an action as failed.

        Args:
            action_id: Action ID
            result: Optional result dict (e.g., error info) to store with the action

        Returns:
            Updated action dictionary
        """
        data: Dict[str, Any] = {}
        if result is not None:
            data['result'] = result
        return self.client._request('POST', f'/api/v1/actions/{action_id}/fail', json=data)


class StatusAPI:
    """Status and health API endpoints"""

    def __init__(self, client: 'OctopoidSDK'):
        self.client = client

    def health(self) -> Dict[str, Any]:
        """Get server health status"""
        return self.client._request('GET', '/api/health')


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
        timeout: int = 30,
        scope: Optional[str] = None
    ):
        self.server_url = server_url.rstrip('/')
        self.api_key = api_key
        self.timeout = timeout
        self.scope = scope
        self.session = requests.Session()

        if api_key:
            self.session.headers['Authorization'] = f'Bearer {api_key}'

        # Initialize API endpoints
        self.tasks = TasksAPI(self)
        self.drafts = DraftsAPI(self)
        self.projects = ProjectsAPI(self)
        self.flows = FlowsAPI(self)
        self.messages = MessagesAPI(self)
        self.actions = ActionsAPI(self)
        self.status = StatusAPI(self)

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[Dict] = None,
        json: Optional[Dict] = None
    ) -> Any:
        """Make HTTP request to API"""
        # Auto-inject scope into requests when set
        if self.scope:
            if method == 'GET':
                if params is None:
                    params = {}
                params.setdefault('scope', self.scope)
            elif json is not None:
                json.setdefault('scope', self.scope)

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

    def poll(self, orchestrator_id: str) -> Dict[str, Any]:
        """Get all scheduler state in a single call.

        Returns queue counts, provisional tasks, and orchestrator registration status.
        Callers should handle exceptions gracefully (poll endpoint may not exist yet on older servers).

        Args:
            orchestrator_id: Orchestrator identifier (passed as ?orchestrator_id=<id>)

        Returns:
            Dict with keys:
              - queue_counts: {incoming: int, claimed: int, provisional: int}
              - provisional_tasks: list of task dicts with id, hooks, pr_number
              - orchestrator_registered: bool
        """
        return self._request(
            'GET',
            '/api/v1/scheduler/poll',
            params={'orchestrator_id': orchestrator_id},
        )

    def close(self):
        """Close the session"""
        self.session.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
