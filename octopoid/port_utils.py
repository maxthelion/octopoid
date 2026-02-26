"""Port allocation utilities for agents."""

from .config import BASE_PORT, PORT_STRIDE


def get_dev_port(agent_id: int) -> int:
    """Get the development server port for an agent.

    Args:
        agent_id: Numeric ID of the agent (0-indexed)

    Returns:
        Port number for development server
    """
    return BASE_PORT + agent_id * PORT_STRIDE + 0


def get_mcp_port(agent_id: int) -> int:
    """Get the MCP (Model Context Protocol) port for an agent.

    Args:
        agent_id: Numeric ID of the agent (0-indexed)

    Returns:
        Port number for MCP server
    """
    return BASE_PORT + agent_id * PORT_STRIDE + 1


def get_playwright_ws_port(agent_id: int) -> int:
    """Get the Playwright WebSocket port for an agent.

    Args:
        agent_id: Numeric ID of the agent (0-indexed)

    Returns:
        Port number for Playwright WebSocket
    """
    return BASE_PORT + agent_id * PORT_STRIDE + 2


def get_all_ports(agent_id: int) -> dict[str, int]:
    """Get all port allocations for an agent.

    Args:
        agent_id: Numeric ID of the agent (0-indexed)

    Returns:
        Dictionary with port allocations
    """
    return {
        "dev_port": get_dev_port(agent_id),
        "mcp_port": get_mcp_port(agent_id),
        "playwright_ws_port": get_playwright_ws_port(agent_id),
    }


def get_port_env_vars(agent_id: int) -> dict[str, str]:
    """Get environment variables for port allocation.

    Args:
        agent_id: Numeric ID of the agent (0-indexed)

    Returns:
        Dictionary of environment variable name to value
    """
    ports = get_all_ports(agent_id)
    return {
        "AGENT_DEV_PORT": str(ports["dev_port"]),
        "AGENT_MCP_PORT": str(ports["mcp_port"]),
        "AGENT_PW_WS_PORT": str(ports["playwright_ws_port"]),
    }
