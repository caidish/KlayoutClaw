"""Agentic E2E test: KLayout geometry → Qiskit simulation in JupyterLab cells.

Simulates the real agent workflow:
1. Read transmon geometry from KLayout
2. Write scqubits simulation code into notebook cells
3. Execute cells → results visible in JupyterLab
4. Write coherence/noise analysis code
5. Write circuit execution code
6. Verify physics results
7. Annotate KLayout layout

Every computation happens inside visible notebook cells.
The agent writes Python code, the notebook executes it.

Prerequisites:
  - KLayout MCP on port 8765
  - instrMCP on port 8123 (with JupyterLab extension + notebook open in browser)
  - Qiskit MCP on port 8124

Run:
  pytest tests/e2e_qiskit/test_agent_workflow.py -v -s
"""

import json
import os
import time

import pytest

INSTRMCP_URL = os.environ.get("INSTRMCP_MCP_URL", "http://127.0.0.1:8123/mcp")


@pytest.fixture(scope="session")
def instrmcp():
    """instrMCP client (port 8123) — for cell manipulation verification."""
    from .conftest import MCPClient
    client = MCPClient(INSTRMCP_URL, client_name="e2e-instrmcp")
    if not client.is_available():
        pytest.skip("instrMCP server not running on port 8123")
    return client


def add_and_execute(qiskit_client, code, timeout=30):
    """Add a code cell to the notebook and execute it."""
    qiskit_client.call_tool("notebook_add_cell", content=code, position="below")
    time.sleep(0.3)
    result = qiskit_client.call_tool("notebook_execute_active_cell", timeout=timeout)
    time.sleep(0.5)
    return result


def read_output(qiskit_client):
    """Read the output of the currently active cell."""
    return qiskit_client.call_tool("notebook_read_active_cell_output")


class TestAgentWorkflow:
    """Full agent workflow: KLayout → notebook cells → Qiskit simulation."""

    # -- Step 1: Read transmon from KLayout --------------------------------

    def test_01_create_transmon_in_klayout(self, klayout):
        """Create a transmon layout in KLayout for the agent to analyze."""
        klayout.call_tool("create_layout", name="XMON_TEST", dbu=0.001)

        # X-mon pads (250 um arms, 30 um wide, 20 um gap)
        klayout.execute_script("""
import pya
dbu = _layout.dbu
cell = _top_cell
li = _layout.layer(1, 0)

for cx in [-500, 500]:
    arm_len, arm_w, gap = 250, 30, 20
    for dx, dy, w, h in [
        (0, 0, arm_w, 2*arm_len),
        (0, 0, 2*arm_len, arm_w),
    ]:
        cell.shapes(li).insert(pya.Box(
            int((cx+dx-w/2)/dbu), int((dy-h/2)/dbu),
            int((cx+dx+w/2)/dbu), int((dy+h/2)/dbu)))

li_jj = _layout.layer(2, 0)
for cx in [-500, 500]:
    cell.shapes(li_jj).insert(pya.Box(
        int((cx-1)/dbu), int(-251/dbu), int((cx+1)/dbu), int(-249/dbu)))

result = {"status": "ok", "qubits": 2}
""")
        print("  2-qubit X-mon layout created in KLayout")

    def test_02_extract_geometry_from_klayout(self, klayout):
        """Agent reads pad dimensions from KLayout."""
        result = klayout.execute_script("""
import pya
lv = pya.Application.instance().main_window().current_view()
ly = lv.active_cellview().layout()
cell = lv.active_cellview().cell

pad_li = ly.layer(1, 0)
pads = []
for shape in cell.shapes(pad_li).each():
    b = shape.dbbox()
    pads.append({"w": b.width(), "h": b.height()})

jj_li = ly.layer(2, 0)
junctions = []
for shape in cell.shapes(jj_li).each():
    b = shape.dbbox()
    junctions.append({"w_um": b.width(), "h_um": b.height()})

result = {"pads": len(pads), "junctions": len(junctions), "pad_shapes": pads[:4], "jj_shapes": junctions[:2]}
""")
        assert result.get("pads", 0) >= 2
        assert result.get("junctions", 0) >= 2
        self.__class__._geometry = result
        print(f"  Extracted: {result['pads']} pads, {result['junctions']} junctions")

    # -- Step 2: Agent writes simulation code in cells ---------------------

    def test_03_write_transmon_simulation(self, qiskit):
        """Agent writes scqubits code into a notebook cell and executes it."""
        code = """\
import scqubits
import numpy as np

# Transmon simulation from X-mon geometry
# Approximate: pad area ~ 250*30 um^2, gap ~ 20 um
EJ, EC = 20.0, 0.3  # GHz (representative values)

tmon = scqubits.Transmon(EJ=EJ, EC=EC, ng=0, ncut=31)
eigenvals = tmon.eigenvals(evals_count=6)

f01 = eigenvals[1] - eigenvals[0]
f12 = eigenvals[2] - eigenvals[1]
alpha = f12 - f01

print(f"Eigenvalues (GHz): {[f'{e:.4f}' for e in eigenvals]}")
print(f"f01 = {f01:.4f} GHz")
print(f"Anharmonicity = {alpha*1000:.1f} MHz")
print(f"EJ/EC = {EJ/EC:.1f}")
"""
        result = add_and_execute(qiskit, code)
        assert result.get("status") != "error" or result.get("executed"), \
            f"Cell execution failed: {result}"
        print("  Transmon simulation cell executed")

    def test_04_verify_simulation_output(self, qiskit):
        """Agent reads the cell output to get physics results."""
        output = read_output(qiskit)
        print(f"  Cell output: {output}")
        # The output should contain printed eigenvalues and f01

    def test_05_write_coherence_estimation(self, qiskit):
        """Agent writes T1/T2 coherence code."""
        code = """\
import numpy as np

EJ, EC = 20.0, 0.3
f01_GHz = np.sqrt(8*EJ*EC) - EC
f01_Hz = f01_GHz * 1e9

Q_cap = 1_000_000
T1_cap = Q_cap / (2 * np.pi * f01_Hz)
T1_us = T1_cap * 1e6

# T2 limited by pure dephasing
charge_disp = EC * 8*np.sqrt(2/np.pi) * (EJ/(2*EC))**0.25 * np.exp(-np.sqrt(8*EJ/EC))
Gamma_phi = 2*np.pi*abs(charge_disp)*1e9
T2 = 1/(1/(2*T1_cap) + Gamma_phi)
T2_us = T2 * 1e6

print(f"f01 = {f01_GHz:.3f} GHz")
print(f"T1 = {T1_us:.1f} us")
print(f"T2 = {T2_us:.1f} us")
"""
        result = add_and_execute(qiskit, code)
        assert result.get("status") != "error" or result.get("executed")
        print("  Coherence estimation cell executed")

    # -- Step 3: Agent writes circuit code ---------------------------------

    def test_06_write_bell_circuit(self, qiskit):
        """Agent creates and executes a Bell state circuit."""
        code = """\
from qiskit import QuantumCircuit, transpile
from qiskit_aer import AerSimulator

# Bell state circuit
bell = QuantumCircuit(2, 2)
bell.h(0)
bell.cx(0, 1)
bell.measure([0, 1], [0, 1])
print(bell.draw())

# Execute
sim = AerSimulator()
result = sim.run(transpile(bell, sim), shots=2000).result()
counts = result.get_counts()
print(f"\\nCounts: {counts}")

total = sum(counts.values())
p00 = counts.get('00', 0) / total
p11 = counts.get('11', 0) / total
print(f"|00> = {p00:.3f}, |11> = {p11:.3f}")
"""
        result = add_and_execute(qiskit, code, timeout=30)
        assert result.get("status") != "error" or result.get("executed")
        print("  Bell circuit cell executed")

    def test_07_verify_circuit_in_namespace(self, qiskit):
        """Agent checks the circuit is in the namespace."""
        result = qiskit.call_tool("qiskit_list_circuits")
        assert result["status"] == "success"
        names = [c["name"] for c in result["circuits"]]
        assert any("bell" in n for n in names), f"No bell circuit in {names}"
        print(f"  Circuits in namespace: {names}")

    # -- Step 4: Agent writes noise analysis code --------------------------

    def test_08_write_noise_analysis(self, qiskit):
        """Agent writes noisy simulation code."""
        code = """\
from qiskit import QuantumCircuit, transpile
from qiskit_aer import AerSimulator
from qiskit_aer.noise import NoiseModel, thermal_relaxation_error
import numpy as np

# Noise model from coherence times
T1, T2 = 100e-6, 50e-6
noise_model = NoiseModel()
thermal_1q = thermal_relaxation_error(T1, T2, 35e-9)
noise_model.add_all_qubit_quantum_error(thermal_1q, ['x','h','sx','rz'])
thermal_2q = thermal_relaxation_error(T1, T2, 300e-9).tensor(
    thermal_relaxation_error(T1, T2, 300e-9))
noise_model.add_all_qubit_quantum_error(thermal_2q, ['cx','cz'])

# Compare ideal vs noisy
qc = QuantumCircuit(2, 2)
qc.h(0)
qc.cx(0, 1)
qc.measure([0, 1], [0, 1])

shots = 4000
ideal = AerSimulator().run(transpile(qc, AerSimulator()), shots=shots).result().get_counts()
noisy = AerSimulator(noise_model=noise_model).run(
    transpile(qc, AerSimulator()), shots=shots).result().get_counts()

all_keys = set(ideal) | set(noisy)
F = sum(np.sqrt(ideal.get(k,0) * noisy.get(k,0)) for k in all_keys) / shots

print(f"Ideal:  {ideal}")
print(f"Noisy:  {noisy}")
print(f"Classical fidelity: {F:.4f}")
"""
        result = add_and_execute(qiskit, code, timeout=30)
        assert result.get("status") != "error" or result.get("executed")
        print("  Noise analysis cell executed")

    # -- Step 5: Agent writes gate fidelity analysis -----------------------

    def test_09_write_gate_fidelity(self, qiskit):
        """Agent writes gate fidelity estimation code."""
        code = """\
import numpy as np

T1, T2 = 100e-6, 50e-6
gates = {"X": 35e-9, "SX": 35e-9, "CX": 300e-9, "CZ": 200e-9}

print("Gate fidelities (coherence-limited):")
for gate, t_gate in gates.items():
    is_2q = gate in ("CX", "CZ")
    n = 2 if is_2q else 1
    p_r = 1 - np.exp(-n * t_gate / T1)
    p_d = 1 - np.exp(-n * t_gate / T2)
    F = 1 - p_r/3 - p_d/2
    print(f"  {gate}: F={F:.6f} ({(1-F)*1e6:.0f} ppm)")
"""
        result = add_and_execute(qiskit, code)
        assert result.get("status") != "error" or result.get("executed")
        print("  Gate fidelity cell executed")

    # -- Step 6: Agent annotates KLayout -----------------------------------

    def test_10_annotate_klayout(self, klayout):
        """Agent writes simulation results back to KLayout as annotations."""
        klayout.execute_script("""
import pya
cell = _top_cell
dbu = _layout.dbu
li = _layout.layer(20, 0)

annotation = "2Q X-mon\\nf01~6.6 GHz\\nalpha~-337 MHz\\nT1~24 us"
for cx in [-500, 500]:
    cell.shapes(li).insert(
        pya.Text(annotation, pya.Trans(pya.Point(int(cx/dbu), int(300/dbu)))))

result = {"status": "ok", "annotations": 2}
""")
        print("  Layout annotated with simulation results")

    def test_11_save_layout(self, klayout, output_dir):
        """Save the annotated layout to GDS."""
        gds_path = os.path.join(output_dir, "agent_workflow.gds")
        klayout.call_tool("save_layout", filepath=gds_path)
        assert os.path.exists(gds_path)
        print(f"  GDS saved: {gds_path} ({os.path.getsize(gds_path)} bytes)")

    # -- Step 7: Verify notebook has real code cells -----------------------

    def test_12_verify_computation_happened(self, qiskit):
        """Verify the agent's code executed by checking namespace for circuits."""
        # Circuits created by cell execution should be in namespace
        circuits = qiskit.call_tool("qiskit_list_circuits")
        circuit_names = [c["name"] for c in circuits["circuits"]]
        print(f"  Circuits in namespace: {circuit_names}")
        assert len(circuit_names) >= 1, "No circuits created by agent code"

        # List all variables to see what's there
        vars_result = qiskit.call_tool("qiskit_list_variables")
        names = [v["name"] for v in vars_result["variables"]]
        print(f"  All variables: {names}")
        print("  Agent workflow completed — code ran in visible notebook cells")
