"""Tests for configurable MCP connection in mcp_client.py.

Tests the legacy fallbacks after the per-user server config is absent.
"""
import json
import os
import sys
import tempfile

import pytest

# Add skills/scripts to path so we can import mcp_client
SKILLS_SCRIPTS = os.path.join(
    os.path.dirname(__file__), "..", "skills", "scripts"
)
sys.path.insert(0, SKILLS_SCRIPTS)

import mcp_client


@pytest.fixture(autouse=True)
def reset_mcp_url(tmp_path, monkeypatch):
    """Reset MCP_URL and isolate tests from the host's per-user config."""
    original = mcp_client.MCP_URL
    monkeypatch.delenv("KLAYOUT_MCP_URL", raising=False)
    monkeypatch.setenv(
        "KLAYOUT_MCP_CONFIG", str(tmp_path / "missing-user-config.json")
    )
    yield
    mcp_client.MCP_URL = original


class TestLoadMcpConfig:
    """Test mcp_client.load_mcp_config() config loading."""

    def test_load_from_explicit_file(self, tmp_path):
        """--mcp-config flag: load URL from a JSON config file."""
        cfg = {"mcpServers": {"klayoutclaw": {"url": "http://10.0.0.1:9999/mcp"}}}
        cfg_file = tmp_path / "custom_mcp.json"
        cfg_file.write_text(json.dumps(cfg))

        url = mcp_client.load_mcp_config(str(cfg_file))
        assert url == "http://10.0.0.1:9999/mcp"

    def test_load_sets_module_mcp_url(self, tmp_path):
        """load_mcp_config should update the module-level MCP_URL."""
        cfg = {"mcpServers": {"klayoutclaw": {"url": "http://custom:1234/mcp"}}}
        cfg_file = tmp_path / "cfg.json"
        cfg_file.write_text(json.dumps(cfg))

        mcp_client.load_mcp_config(str(cfg_file))
        assert mcp_client.MCP_URL == "http://custom:1234/mcp"

    def test_fallback_to_cwd_mcp_json(self, tmp_path, monkeypatch):
        """When no explicit file given, look for .mcp.json in CWD."""
        cfg = {"mcpServers": {"klayoutclaw": {"url": "http://fromcwd:5555/mcp"}}}
        cfg_file = tmp_path / ".mcp.json"
        cfg_file.write_text(json.dumps(cfg))

        monkeypatch.chdir(tmp_path)

        url = mcp_client.load_mcp_config(None)
        assert url == "http://fromcwd:5555/mcp"

    def test_fallback_to_project_root_config(self, tmp_path, monkeypatch):
        """When no .mcp.json in CWD, look for mcp_config.json in project root."""
        # Create a fake project structure: tmp_path/skills/scripts/
        scripts_dir = tmp_path / "skills" / "scripts"
        scripts_dir.mkdir(parents=True)
        cfg = {"mcpServers": {"klayoutclaw": {"url": "http://fromroot:6666/mcp"}}}
        cfg_file = tmp_path / "mcp_config.json"
        cfg_file.write_text(json.dumps(cfg))

        # Point _script_dir to the fake scripts dir so project root resolves to tmp_path
        monkeypatch.setattr(
            mcp_client, "_script_dir",
            lambda: str(scripts_dir),
        )
        # CWD has no .mcp.json
        empty_cwd = tmp_path / "empty_cwd"
        empty_cwd.mkdir()
        monkeypatch.chdir(empty_cwd)

        url = mcp_client.load_mcp_config(None)
        assert url == "http://fromroot:6666/mcp"

    def test_fallback_to_default_when_no_config(self, tmp_path, monkeypatch):
        """When no config file exists anywhere, use default 127.0.0.1:8765."""
        # CWD has no .mcp.json, project root has no mcp_config.json
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(
            mcp_client, "_script_dir",
            lambda: tempfile.mkdtemp(),
        )

        url = mcp_client.load_mcp_config(None)
        assert url == "http://127.0.0.1:8765/mcp"

    def test_explicit_file_not_found_raises(self):
        """If --mcp-config points to a missing file, raise FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            mcp_client.load_mcp_config("/nonexistent/path/config.json")

    def test_invalid_json_raises(self, tmp_path):
        """If config file has invalid JSON, raise ValueError."""
        bad_file = tmp_path / "bad.json"
        bad_file.write_text("not json {{{")

        with pytest.raises((json.JSONDecodeError, ValueError)):
            mcp_client.load_mcp_config(str(bad_file))

    def test_single_unknown_server_uses_fallback(self, tmp_path):
        """A single server entry is unambiguous even under an unknown label."""
        cfg = {"mcpServers": {"other_server": {"url": "http://x:1/mcp"}}}
        cfg_file = tmp_path / "cfg.json"
        cfg_file.write_text(json.dumps(cfg))

        url = mcp_client.load_mcp_config(str(cfg_file))

        assert url == "http://x:1/mcp"
        assert mcp_client.MCP_URL == "http://x:1/mcp"


class TestCommitGdsCliFlag:
    """Test that commit_gds.py accepts --mcp-config and passes it through."""

    def test_argparse_accepts_mcp_config(self):
        """commit_gds.py argparser should accept --mcp-config."""
        sys.path.insert(0, os.path.join(
            os.path.dirname(__file__), "..", "skills",
            "nanodevice_gdsalign", "scripts"
        ))
        import commit_gds
        # Build parser and parse with --mcp-config
        parser = commit_gds.build_parser()
        args = parser.parse_args([
            "--warp", "w.npy", "--traces", "t.json",
            "--image", "img.jpg", "--pixel-size", "0.087",
            "--gds", "t.gds", "--output-dir", "/tmp/out",
            "--mcp-config", "/tmp/my_config.json",
        ])
        assert args.mcp_config == "/tmp/my_config.json"

    def test_argparse_mcp_config_optional(self):
        """--mcp-config should be optional (default None)."""
        sys.path.insert(0, os.path.join(
            os.path.dirname(__file__), "..", "skills",
            "nanodevice_gdsalign", "scripts"
        ))
        import commit_gds
        parser = commit_gds.build_parser()
        args = parser.parse_args([
            "--warp", "w.npy", "--traces", "t.json",
            "--image", "img.jpg", "--pixel-size", "0.087",
            "--gds", "t.gds", "--output-dir", "/tmp/out",
        ])
        assert args.mcp_config is None
