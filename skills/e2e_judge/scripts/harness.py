#!/usr/bin/env python3
"""Agentic harness — invokes Claude Code with a natural-language task
and captures the full agent transcript.
"""

import json
import os
import subprocess
import time
from dataclasses import dataclass

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", "..", ".."))
DEFAULT_MCP_CONFIG = os.path.join(REPO_ROOT, "mcp_config.json")
DEFAULT_MODEL = os.environ.get("E2E_AGENT_MODEL", "sonnet")
DEFAULT_AGENT = os.environ.get("E2E_AGENT", "qlaybot")
KLAYOUT_MCP_URL = os.environ.get("KLAYOUT_MCP_URL", "http://127.0.0.1:8765/mcp")


@dataclass
class AgentResult:
    transcript: str
    duration: float
    timed_out: bool
    returncode: int

    @property
    def success(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def transcript_excerpt(self, max_chars: int = 4000) -> str:
        if len(self.transcript) <= max_chars:
            return self.transcript
        return self.transcript[:max_chars] + f"\n... [truncated, {len(self.transcript)} total chars]"


def run_agent(prompt: str, timeout: int = 120, mcp_config: str = None,
              model: str = None, agent: str = None) -> AgentResult:
    """Invoke an agent with a natural-language prompt.

    Supports two agents:
    - "qlaybot": uses `qlaybot -m <prompt>` (default)
    - "claude":  uses `claude --print --mcp-config ... <prompt>`

    The agent autonomously decides which MCP tools to call.
    Returns the full agent transcript (stdout + stderr).
    """
    config = mcp_config or DEFAULT_MCP_CONFIG
    if mcp_config is None and os.environ.get("KLAYOUT_MCP_URL"):
        config = json.dumps({
            "mcpServers": {
                "klayoutclaw": {
                    "type": "http",
                    "url": KLAYOUT_MCP_URL,
                },
            },
        })
    mdl = model or DEFAULT_MODEL
    agent_type = agent or DEFAULT_AGENT

    if agent_type == "qlaybot":
        cmd = [
            "qlaybot",
            "-m", prompt,
        ]
    else:
        cmd = [
            "claude",
            "--print",
            "--mcp-config", config,
            "--dangerously-skip-permissions",
            "--model", mdl,
            "--bare",
            prompt,
        ]

    start = time.time()
    timed_out = False
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=REPO_ROOT,
        )
        transcript = proc.stdout
        # For qlaybot JSON mode, extract the response text
        if agent_type == "qlaybot" and transcript.strip().startswith("{"):
            try:
                result = json.loads(transcript)
                transcript = result.get("response", transcript)
            except (json.JSONDecodeError, KeyError):
                pass
        if proc.stderr:
            transcript += f"\n[stderr]\n{proc.stderr}"
        returncode = proc.returncode
    except subprocess.TimeoutExpired as e:
        timed_out = True
        transcript = (e.stdout or b"").decode("utf-8", errors="replace")
        if e.stderr:
            transcript += f"\n[stderr]\n{e.stderr.decode('utf-8', errors='replace')}"
        transcript += f"\n[TIMEOUT after {timeout}s]"
        returncode = -1
    except FileNotFoundError:
        timed_out = False
        cli_name = "qlaybot" if agent_type == "qlaybot" else "claude"
        transcript = f"[ERROR] '{cli_name}' CLI not found. Ensure it is installed and on PATH."
        returncode = -2
    except Exception as e:
        timed_out = False
        transcript = f"[ERROR] {e}"
        returncode = -3

    duration = time.time() - start
    return AgentResult(
        transcript=transcript,
        duration=duration,
        timed_out=timed_out,
        returncode=returncode,
    )


def check_mcp_server(url: str = None) -> bool:
    """Quick connectivity check — is the KLayout MCP server reachable?"""
    from verifier import MCPClient
    client = MCPClient(url or KLAYOUT_MCP_URL)
    return client.is_available()


def reset_layout(client, name: str = "AGENTIC_TEST"):
    """Create a fresh layout via direct MCP call."""
    client.call_tool("create_layout", name=name, dbu=0.001)
