# Qiskit MCP Server

Jupyter-embedded MCP server for quantum device simulation workflows with KLayoutClaw. Provides namespace queries and resource guides. The agent writes Python code in JupyterLab notebook cells via [instrMCP](https://github.com/caidish/instrMCP) — every computation is visible, modifiable, and reproducible.

---

## Architecture

Three MCP servers work together:

```
┌──────────────┐
│  Claude Code │
│  (Agent)     │
└──┬───┬───┬───┘
   │   │   │
   │   │   └── HTTP :8124 ── Qiskit MCP ── namespace queries + resource guides
   │   │
   │   └────── HTTP :8123 ── instrMCP ── notebook cell manipulation
   │
   └────────── HTTP :8765 ── KLayout MCP ── layout geometry
```

| Server | Port | Role |
|--------|------|------|
| KLayout MCP | 8765 | Read/write layout geometry (pya scripts, GDS save, screenshot) |
| instrMCP | 8123 | Write and execute code in JupyterLab notebook cells |
| Qiskit MCP | 8124 | Query namespace for circuits/variables, serve code example guides |

The agent writes Python code into visible notebook cells using instrMCP's cell tools, queries the kernel namespace via Qiskit MCP, and reads/writes layout geometry via KLayout MCP.

---

## Prerequisites & Installation

### 1. KLayout with KlayoutClaw plugin

```bash
cd KLayoutClaw
python install.py
open /Applications/klayout.app
```

### 2. Python environment

```bash
# Qiskit MCP server (installs qiskit-mcp package)
cd KLayoutClaw/servers/qiskit_mcp
pip install -e .

# instrMCP (cell manipulation + JupyterLab extension)
# https://github.com/caidish/instrMCP
pip install -e /path/to/instrMCP
instrmcp-setup

# Simulation libraries (used by the agent's code in notebook cells)
pip install qiskit qiskit-aer scqubits

# JupyterLab
pip install jupyterlab
```

### 3. Verify

```bash
python -c "
import qiskit_mcp; print(f'qiskit-mcp {qiskit_mcp.__version__}')
import instrmcp; print(f'instrmcp {instrmcp.__version__}')
import qiskit; print(f'qiskit {qiskit.__version__}')
import scqubits; print(f'scqubits {scqubits.__version__}')
import qiskit_aer; print(f'qiskit-aer {qiskit_aer.__version__}')
"
```

---

## Starting the Servers

### 1. Start JupyterLab

```bash
jupyter lab --notebook-dir=/path/to/working/dir --port=8889 --no-browser
```

Open the URL in your browser. The notebook **must be open in the browser** for instrMCP's cell bridge to function.

### 2. In a notebook, start both MCP servers

```python
# Cell 1: instrMCP
%load_ext instrmcp.extensions
%mcp_start

# Cell 2: Qiskit MCP
%load_ext qiskit_mcp.jupyter_mcp_extension
%qiskit_mcp_start
```

### 3. Verify

```bash
python tests/test_qiskit_mcp.py --both
```

---

## Connecting Claude Code

### mcp_config.json

```json
{
  "klayoutclaw": {"type": "http", "url": "http://127.0.0.1:8765/mcp"},
  "instrmcp": {"type": "http", "url": "http://127.0.0.1:8123/mcp"},
  "qiskit": {"type": "http", "url": "http://127.0.0.1:8124/mcp"}
}
```

```bash
claude --mcp-config mcp_config.json
```

### Claude Code CLI

```bash
claude mcp add --transport http klayoutclaw http://127.0.0.1:8765/mcp
claude mcp add --transport http instrmcp http://127.0.0.1:8123/mcp
claude mcp add --transport http qiskit http://127.0.0.1:8124/mcp
```

### .mcp.json (Claude Desktop, STDIO)

```json
{
  "mcpServers": {
    "klayoutclaw": {
      "command": "npx",
      "args": ["mcp-remote", "http://127.0.0.1:8765/mcp", "--allow-http"]
    },
    "qiskit": {
      "command": "npx",
      "args": ["mcp-remote", "http://127.0.0.1:8124/mcp", "--allow-http"]
    }
  }
}
```

---

## Tool Reference

### Cell Manipulation (instrMCP, port 8123)

| Tool | Parameters | Returns |
|------|-----------|---------|
| `notebook_add_cell` | `content` (str), `cell_type` ("code"/"markdown"), `position` ("above"/"below") | `{success}` |
| `notebook_execute_active_cell` | `timeout` (float, default 30) | `{status, executed, has_error, has_output, outputs}` |
| `notebook_read_active_cell` | — | `{cell_content, cell_type, index, notebook_path}` |
| `notebook_read_active_cell_output` | — | `{has_output, outputs: [{type, text}]}` |
| `notebook_apply_patch` | `old_text` (str), `new_text` (str) | `{success}` |
| `notebook_delete_cell` | `cell_id_notebooks` (str, optional JSON int or list) | `{success}` |
| `notebook_move_cursor` | `target` ("above"/"below"/"bottom"/"index:N") | `{success}` |

### Namespace Queries (Qiskit MCP, port 8124)

| Tool | Parameters | Returns |
|------|-----------|---------|
| `qiskit_list_circuits` | `type_filter` (str, optional) | `{circuits: [{name, num_qubits, num_clbits, depth, size, num_parameters}], count}` |
| `qiskit_circuit_info` | `name` (str), `detailed` (bool) | `{name, num_qubits, depth, gate_counts, diagram, parameters}` |
| `qiskit_list_backends` | — | `{backends: [{name, description}], count}` |
| `qiskit_list_variables` | `type_filter` (str, optional), `max_items` (int) | `{variables: [{name, type, repr}], count}` |
| `qiskit_read_variable` | `name` (str), `detailed` (bool) | `{name, type, repr, attributes}` |

### Layout Geometry (KLayout MCP, port 8765)

| Tool | Parameters | Returns |
|------|-----------|---------|
| `create_layout` | `name` (str), `dbu` (float) | `{status, top_cell, dbu}` |
| `execute_script` | `code` (str) | Value of `result` variable (dict) |
| `save_layout` | `filepath` (str) | `{status}` |
| `get_layout_info` | — | `{dbu, num_cells, cells, num_layers}` |
| `screenshot` | `filepath` (str) | `{status}` |

---

## Resource Reference

Resources are code example guides served by Qiskit MCP. The agent reads them before writing code.

| Resource URI | Content |
|---|---|
| `resource://qiskit_transmon_guide` | scqubits: Transmon creation, eigenvalues, geometry→EC/EJ, charge dispersion spectrum, tunable transmon flux sweep, coherence estimation, matrix elements. Includes parameter ranges. |
| `resource://qiskit_noise_channels` | qiskit-aer: NoiseModel construction (thermal relaxation + depolarizing + readout), noisy vs ideal circuit comparison, gate fidelity formulas, decoherence budget for N-gate sequences. Includes default gate times. |
| `resource://qiskit_circuit_patterns` | Common circuits: Bell (2q), GHZ (Nq), QFT (Nq). AerSimulator execution, statevector inspection, histogram visualization. |
| `resource://qiskit_metal_components` | Qiskit Metal component catalog: TransmonPocket, TransmonCross, RouteMeander, CoupledLineTee. |

Each resource contains complete, copy-paste-ready Python code blocks.

---

## Testing

```bash
# Unit tests (16 tests, ~2s, no servers needed)
pytest tests/test_qiskit_mcp_unit.py -v

# Connection test (requires running servers)
python tests/test_qiskit_mcp.py --both

# E2E agent workflow (12 tests, requires all 3 servers + JupyterLab open in browser)
pytest tests/e2e_qiskit/test_agent_workflow.py -v -s
```

---

## Magic Commands

| Command | Description |
|---------|-------------|
| `%qiskit_mcp_start` | Start server on port 8124 |
| `%qiskit_mcp_start --port 9000` | Start on custom port |
| `%qiskit_mcp_stop` | Stop the server |
| `%qiskit_mcp_restart` | Restart the server |
| `%qiskit_mcp_status` | Show server status |

---

## File Structure

```
servers/qiskit_mcp/
├── pyproject.toml                # pip install -e . (v0.2.0)
├── __init__.py                   # Package version
├── mcp_server.py                 # FastMCP HTTP server (12 tools + 4 resources)
├── jupyter_mcp_extension.py      # Magic commands
├── tools.py                      # QiskitToolsFacade (namespace queries)
├── backend/
│   ├── base.py                   # SharedState, BaseBackend
│   ├── qiskit_core.py            # list_circuits, circuit_info, list_backends
│   └── notebook_unsafe.py        # Cell manipulation via instrMCP bridge
├── core/
│   ├── qiskit_tools.py           # 5 namespace query tool registrations
│   ├── notebook_unsafe_tools.py  # 7 cell manipulation tool registrations
│   └── resources.py              # 4 resource guides with code examples
├── config/
│   └── metadata_baseline.yaml    # Tool/resource metadata
└── launcher/
    ├── stdio_proxy.py            # FastMCP STDIO proxy for Claude Desktop
    └── qiskit_launcher.py        # CLI entry point: qiskit-mcp-launcher
```
