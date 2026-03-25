---
name: qiskit:simulate
description: Simulate quantum device properties from KLayout geometry. The agent reads transmon geometry from KLayout, writes Python simulation code in JupyterLab notebook cells (scqubits, qiskit-aer), executes them, and annotates the layout with results.
---

# qiskit:simulate — Quantum Device Simulation

The agent analyzes a transmon qubit design by writing and executing Python code in visible JupyterLab notebook cells. Every computation is inspectable and reproducible.

## Prerequisites

- KLayout running with KlayoutClaw server on port 8765
- JupyterLab with instrMCP (port 8123) and Qiskit MCP (port 8124)
- Notebook open in browser (for cell visibility)
- Python packages: `qiskit`, `qiskit-aer`, `scqubits`

## Agent Workflow

### Step 1: Read geometry from KLayout

Use KLayout MCP's `execute_script` to extract pad dimensions, gap, and junction size:
```python
# Via KLayout MCP execute_script
pad_li = ly.layer(1, 0)
for shape in cell.shapes(pad_li).each():
    b = shape.dbbox()
    # b.width(), b.height() → pad dimensions in microns
```

### Step 2: Write transmon simulation in a notebook cell

Use `notebook_add_cell` + `notebook_execute_active_cell`:
```python
import scqubits
tmon = scqubits.Transmon(EJ=20.0, EC=0.3, ng=0, ncut=31)
eigenvals = tmon.eigenvals(evals_count=6)
f01 = eigenvals[1] - eigenvals[0]
alpha = (eigenvals[2] - eigenvals[1]) - f01
print(f"f01 = {f01:.3f} GHz, alpha = {alpha*1000:.1f} MHz")
```

Read `resource://qiskit_transmon_guide` for code examples.

### Step 3: Write coherence estimation in a cell

```python
import numpy as np
# T1 from capacitive quality factor
Q_cap = 1_000_000
T1 = Q_cap / (2 * np.pi * f01_Hz)
# T2 from dephasing
...
```

Read `resource://qiskit_noise_channels` for noise modeling code.

### Step 4: Write circuit simulation in cells

```python
from qiskit import QuantumCircuit, transpile
from qiskit_aer import AerSimulator
# Create and execute circuits
...
```

Read `resource://qiskit_circuit_patterns` for circuit examples.

### Step 5: Verify against targets

Write a verification cell comparing results to user's target specs.

### Step 6: Annotate KLayout

Use KLayout MCP's `execute_script` to add text annotations on L20/0:
```python
cell.shapes(li).insert(pya.Text(annotation, pya.Trans(...)))
```

## Resources Available

| URI | Content |
|-----|---------|
| `resource://qiskit_transmon_guide` | scqubits code examples, parameter ranges |
| `resource://qiskit_noise_channels` | Noise model construction, gate fidelity |
| `resource://qiskit_circuit_patterns` | Bell, GHZ, QFT circuits, execution |
| `resource://qiskit_metal_components` | Qiskit Metal component catalog |

## Tools Used

- `execute_script` (KLayout MCP) — read geometry, annotate layout
- `notebook_add_cell` (instrMCP) — write code into notebook
- `notebook_execute_active_cell` (instrMCP) — run the cell
- `notebook_read_active_cell_output` (instrMCP) — read results
- `qiskit_list_circuits` (Qiskit MCP) — check circuits in namespace
- `qiskit_list_variables` (Qiskit MCP) — check variables
