import os
from unittest.mock import patch

def test_bind_default_is_loopback():
    """Without KLAYOUT_MCP_BIND, plugin must keep host-mode behavior."""
    with patch.dict(os.environ, {}, clear=True):
        addr = os.environ.get("KLAYOUT_MCP_BIND", "127.0.0.1")
        assert addr == "127.0.0.1"

def test_bind_env_override():
    """KLAYOUT_MCP_BIND=0.0.0.0 makes the plugin bind all interfaces."""
    with patch.dict(os.environ, {"KLAYOUT_MCP_BIND": "0.0.0.0"}):
        addr = os.environ.get("KLAYOUT_MCP_BIND", "127.0.0.1")
        assert addr == "0.0.0.0"
