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
