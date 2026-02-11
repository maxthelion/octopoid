"""
Octopoid SDK exceptions
"""


class OctopoidError(Exception):
    """Base exception for all Octopoid SDK errors"""

    pass


class OctopoidAPIError(OctopoidError):
    """Raised when API request fails"""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class OctopoidNotFoundError(OctopoidAPIError):
    """Raised when resource is not found (404)"""

    def __init__(self, message: str):
        super().__init__(message, status_code=404)


class OctopoidAuthenticationError(OctopoidAPIError):
    """Raised when authentication fails (401)"""

    def __init__(self, message: str):
        super().__init__(message, status_code=401)
