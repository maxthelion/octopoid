"""SDK initialization and orchestrator identity.

This module provides the central SDK client and orchestrator ID management.
"""

import os
import socket
from typing import Any, Optional

import yaml

from .config import get_orchestrator_dir, get_scope

# Global SDK instance (lazy-initialized)
_sdk: Optional[Any] = None


def get_sdk():
    """Get or initialize SDK client for API operations.

    The server URL is resolved in this order:
    1. OCTOPOID_SERVER_URL env var (useful for tests and CI)
    2. .octopoid/config.yaml server.url

    Returns:
        OctopoidSDK instance

    Raises:
        RuntimeError: If SDK not installed or server not configured
    """
    global _sdk

    if _sdk is not None:
        return _sdk

    try:
        from octopoid_sdk import OctopoidSDK
    except ImportError:
        raise RuntimeError(
            "octopoid-sdk not installed. Install with: pip install octopoid-sdk"
        )

    # Check env var override first (tests, CI, Docker)
    env_url = os.environ.get("OCTOPOID_SERVER_URL")
    if env_url:
        api_key = os.getenv("OCTOPOID_API_KEY")
        scope = get_scope()
        _sdk = OctopoidSDK(server_url=env_url, api_key=api_key, scope=scope)
        return _sdk

    # Load server configuration from config file
    try:
        import yaml
        orchestrator_dir = get_orchestrator_dir()
        config_path = orchestrator_dir.parent / ".octopoid" / "config.yaml"

        if not config_path.exists():
            raise RuntimeError(
                f"No .octopoid/config.yaml found. Run: octopoid init --server <url>"
            )

        with open(config_path) as f:
            config = yaml.safe_load(f)

        server_config = config.get("server", {})
        if not server_config.get("enabled"):
            raise RuntimeError(
                "Server not enabled in .octopoid/config.yaml"
            )

        server_url = server_config.get("url")
        if not server_url:
            raise RuntimeError(
                "Server URL not configured in .octopoid/config.yaml"
            )

        # API key resolution order:
        # 1. .octopoid/.api_key file (gitignored, written by save_api_key())
        # 2. OCTOPOID_API_KEY env var
        api_key_file = config_path.parent / ".api_key"
        api_key = None
        if api_key_file.exists():
            api_key = api_key_file.read_text().strip() or None
        if not api_key:
            api_key = os.getenv("OCTOPOID_API_KEY")
        scope = get_scope()

        _sdk = OctopoidSDK(server_url=server_url, api_key=api_key, scope=scope)
        return _sdk

    except Exception as e:
        raise RuntimeError(f"Failed to initialize SDK: {e}")


def reset_sdk() -> None:
    """Clear the cached SDK instance so it is re-initialised on the next call.

    Call this after storing a new API key so the next
    ``get_sdk()`` picks up the updated credentials.
    """
    global _sdk
    _sdk = None


def get_orchestrator_id() -> str:
    """Get unique orchestrator instance ID.

    Returns:
        Orchestrator ID in format: {cluster}-{machine_id}
    """
    import yaml
    from .config import get_orchestrator_dir

    try:
        orchestrator_dir = get_orchestrator_dir()
        config_path = orchestrator_dir.parent / ".octopoid" / "config.yaml"

        if not config_path.exists():
            # Fallback to hostname if no config
            return socket.gethostname()

        with open(config_path) as f:
            config = yaml.safe_load(f)

        server_config = config.get("server", {})
        cluster = server_config.get("cluster", "default")
        machine_id = server_config.get("machine_id", socket.gethostname())

        return f"{cluster}-{machine_id}"
    except Exception:
        # Fallback to hostname on any error
        return socket.gethostname()
