"""Resource registrar — code example guides for the agent.

These resources teach the agent how to write Python code for
transmon simulation, noise modeling, circuit execution, etc.
The agent reads a resource, then writes code in notebook cells.
"""

import logging

logger = logging.getLogger(__name__)

TRANSMON_GUIDE = """\
# Transmon Simulation Guide (scqubits)

## Quick Start
```python
import scqubits
import numpy as np

# Create transmon with known EJ, EC
tmon = scqubits.Transmon(EJ=20.0, EC=0.3, ng=0.0, ncut=31)
eigenvals = tmon.eigenvals(evals_count=6)

f01 = eigenvals[1] - eigenvals[0]
f12 = eigenvals[2] - eigenvals[1]
alpha = f12 - f01  # anharmonicity

print(f"f01 = {f01:.3f} GHz")
print(f"Anharmonicity = {alpha*1000:.1f} MHz")
print(f"EJ/EC = {20.0/0.3:.1f}")
```

## From Physical Geometry
```python
import numpy as np

# Pad dimensions → capacitance → EC
pad_w = 350e-6   # meters
pad_h = 300e-6
gap = 30e-6
eps0 = 8.854e-12
eps_r = 11.45  # silicon

C = eps0 * eps_r * (pad_w * pad_h) / gap
EC_J = (1.602e-19)**2 / (2 * C)
EC_GHz = EC_J / 6.626e-34 / 1e9

# Junction critical current → EJ
Ic = 30e-9  # 30 nA
Phi0 = 6.626e-34 / (2 * 1.602e-19)
EJ_J = Ic * Phi0 / (2 * np.pi)
EJ_GHz = EJ_J / 6.626e-34 / 1e9

print(f"EC = {EC_GHz:.4f} GHz, EJ = {EJ_GHz:.2f} GHz, EJ/EC = {EJ_GHz/EC_GHz:.1f}")
```

## Charge Dispersion Spectrum
```python
import scqubits
import numpy as np
import matplotlib.pyplot as plt

tmon = scqubits.Transmon(EJ=20, EC=0.3, ng=0, ncut=31)
ng_vals = np.linspace(0, 1, 50)
specdata = tmon.get_spectrum_vs_paramvals("ng", ng_vals, evals_count=4)

fig, ax = plt.subplots()
for i in range(4):
    ax.plot(ng_vals, specdata.energy_table[:, i], label=f"E{i}")
ax.set_xlabel("Offset charge ng")
ax.set_ylabel("Energy (GHz)")
ax.legend()
plt.show()
```

## Tunable Transmon (Flux Sweep)
```python
import scqubits
import numpy as np
import matplotlib.pyplot as plt

EJmax, EC, d = 30.0, 0.3, 0.05
flux_vals = np.linspace(0, 0.5, 50)
freqs = []
for phi in flux_vals:
    EJ_eff = EJmax * abs(np.cos(np.pi * phi)) * np.sqrt(1 + d**2 * np.tan(np.pi * phi)**2)
    if EJ_eff/EC < 1:
        freqs.append(np.nan)
        continue
    tmon = scqubits.Transmon(EJ=EJ_eff, EC=EC, ng=0, ncut=31)
    evals = tmon.eigenvals(evals_count=3)
    freqs.append(evals[1] - evals[0])

plt.plot(flux_vals, freqs)
plt.xlabel("Flux (Phi/Phi0)")
plt.ylabel("Frequency (GHz)")
plt.title("Tunable transmon")
plt.show()
```

## Coherence Estimation
```python
import numpy as np

EJ, EC = 20.0, 0.3  # GHz
f01_GHz = np.sqrt(8*EJ*EC) - EC
f01_Hz = f01_GHz * 1e9
h, kB = 6.626e-34, 1.381e-23

Q_cap = 1e6
T1_cap = Q_cap / (2 * np.pi * f01_Hz)
T1_us = T1_cap * 1e6

# T2 (limited by pure dephasing)
T2_us = min(2 * T1_us, T1_us * 1.5)  # approximate

print(f"f01 = {f01_GHz:.3f} GHz")
print(f"T1 ~ {T1_us:.1f} us, T2 ~ {T2_us:.1f} us")
```

## Matrix Elements
```python
import scqubits
import numpy as np

tmon = scqubits.Transmon(EJ=20, EC=0.3, ng=0, ncut=31)
evals, evecs = tmon.eigensys(evals_count=4)
n_op = tmon.n_operator()

for i in range(4):
    for j in range(4):
        me = abs(np.vdot(evecs[:, i], n_op @ evecs[:, j]))
        if me > 0.001:
            print(f"<{i}|n|{j}> = {me:.4f}")
```

## Parameter Ranges
- EJ: 10-50 GHz (typical), EJ/EC: 20-80 (transmon regime)
- EC: 0.1-0.5 GHz
- f01: 4-8 GHz (typical target)
- Anharmonicity: -200 to -400 MHz
- T1 target: >50 us, T2 target: >30 us
"""

NOISE_GUIDE = """\
# Noise Modeling Guide (qiskit-aer)

## Build a Noise Model
```python
from qiskit_aer.noise import NoiseModel, thermal_relaxation_error, depolarizing_error, ReadoutError
import numpy as np

T1, T2 = 100e-6, 50e-6  # seconds
t_1q, t_2q = 35e-9, 300e-9  # gate times

noise_model = NoiseModel()

# Single-qubit: thermal relaxation + depolarizing
thermal_1q = thermal_relaxation_error(T1, T2, t_1q)
depol_1q = depolarizing_error(0.001)
noise_model.add_all_qubit_quantum_error(thermal_1q.compose(depol_1q), ['x','y','h','sx','rz'])

# Two-qubit: thermal relaxation + depolarizing
thermal_2q = thermal_relaxation_error(T1, T2, t_2q).tensor(thermal_relaxation_error(T1, T2, t_2q))
depol_2q = depolarizing_error(0.01, 2)
noise_model.add_all_qubit_quantum_error(thermal_2q.compose(depol_2q), ['cx','cz'])

# Readout error
noise_model.add_all_qubit_readout_error(ReadoutError([[0.99, 0.01], [0.01, 0.99]]))

print(f"Noise model ready: {noise_model.basis_gates}")
```

## Noisy vs Ideal Simulation
```python
from qiskit import QuantumCircuit, transpile
from qiskit_aer import AerSimulator
import numpy as np

# Create circuit
qc = QuantumCircuit(2, 2)
qc.h(0)
qc.cx(0, 1)
qc.measure([0, 1], [0, 1])

shots = 4096

# Ideal
ideal_sim = AerSimulator()
ideal_counts = ideal_sim.run(transpile(qc, ideal_sim), shots=shots).result().get_counts()

# Noisy (use noise_model from above)
noisy_sim = AerSimulator(noise_model=noise_model)
noisy_counts = noisy_sim.run(transpile(qc, noisy_sim), shots=shots).result().get_counts()

# Classical fidelity
all_keys = set(ideal_counts) | set(noisy_counts)
F = sum(np.sqrt(ideal_counts.get(k,0) * noisy_counts.get(k,0)) for k in all_keys) / shots

print(f"Ideal:  {ideal_counts}")
print(f"Noisy:  {noisy_counts}")
print(f"Classical fidelity: {F:.4f}")
```

## Gate Fidelity Estimation
```python
import numpy as np

T1, T2 = 100e-6, 50e-6  # seconds
gates = {"X": 35e-9, "SX": 35e-9, "CX": 300e-9, "CZ": 200e-9}

for gate, t_gate in gates.items():
    is_2q = gate in ("CX", "CZ")
    n = 2 if is_2q else 1
    p_r = 1 - np.exp(-n * t_gate / T1)
    p_d = 1 - np.exp(-n * t_gate / T2)
    F = 1 - p_r/3 - p_d/2
    print(f"{gate}: F={F:.6f} ({(1-F)*1e6:.0f} ppm infidelity)")
```

## Decoherence Budget
```python
import numpy as np

T1, T2 = 100e-6, 50e-6
t_gate = 35e-9
n_gates = 100
t_total = n_gates * t_gate

p_relax = 1 - np.exp(-t_total / T1)
p_dephase = 1 - np.exp(-t_total / T2)
F_alg = (1 + np.exp(-t_total/T1) + np.exp(-t_total/T2)) / 3

print(f"{n_gates}-gate sequence ({t_total*1e6:.2f} us):")
print(f"  P(relax) = {p_relax:.4f}, P(dephase) = {p_dephase:.4f}")
print(f"  Est. fidelity = {F_alg:.4f}")
```

## Noise Channel Physics
- T1 (relaxation): Q_cap/Q_ind → 1/(2πf01), Purcell: (g/Δ)²κ
- T2 (dephasing): T2 ≤ 2T1, limited by charge/flux/photon noise
- Charge dephasing: exponentially suppressed in transmon regime
"""

METAL_GUIDE = """\
# Qiskit Metal Component Guide

## Components
- TransmonPocket: pad_width, pad_height, pad_gap
- TransmonCross: cross_width, cross_length, cross_gap
- RouteMeander: CPW meander resonator
- CoupledLineTee: T-junction coupler
- RoutePathfinder: Auto-routed CPW

## Note
Qiskit Metal requires a separate installation and GUI.
For KLayout-based designs, use KLayout MCP tools instead.
The agent can extract geometry from KLayout and simulate
using scqubits/qiskit-aer code (see other resources).
"""

CIRCUIT_PATTERNS = """\
# Common Circuit Patterns

## Bell State
```python
from qiskit import QuantumCircuit
qc = QuantumCircuit(2, 2)
qc.h(0)
qc.cx(0, 1)
qc.measure([0, 1], [0, 1])
print(qc.draw())
```

## GHZ State (N qubits)
```python
from qiskit import QuantumCircuit
n = 3
qc = QuantumCircuit(n, n)
qc.h(0)
for i in range(n-1):
    qc.cx(i, i+1)
qc.measure_all(add_bits=False)
print(qc.draw())
```

## QFT
```python
from qiskit import QuantumCircuit
import math
n = 3
qc = QuantumCircuit(n)
for i in range(n):
    qc.h(i)
    for j in range(i+1, n):
        qc.cp(math.pi / 2**(j-i), j, i)
for i in range(n//2):
    qc.swap(i, n-1-i)
print(qc.draw())
```

## Execute on Simulator
```python
from qiskit import transpile
from qiskit_aer import AerSimulator

sim = AerSimulator()
result = sim.run(transpile(qc, sim), shots=1024).result()
counts = result.get_counts()
print(counts)

# Visualization
from qiskit.visualization import plot_histogram
plot_histogram(counts)
```

## Statevector Inspection
```python
from qiskit_aer import StatevectorSimulator

sv_sim = StatevectorSimulator()
result = sv_sim.run(transpile(qc, sv_sim)).result()
sv = result.get_statevector()
print(sv)
```
"""


class ResourceRegistrar:
    """Registers MCP resources — code example guides for the agent."""

    def __init__(self, mcp_server):
        self.mcp = mcp_server

    def register_all(self):
        self._register_transmon_guide()
        self._register_noise_guide()
        self._register_metal_guide()
        self._register_circuit_patterns()

    def _register_transmon_guide(self):
        @self.mcp.resource("resource://qiskit_transmon_guide")
        def transmon_guide() -> str:
            """Transmon simulation with scqubits — parameter ranges, geometry estimation, code examples."""
            return TRANSMON_GUIDE

    def _register_noise_guide(self):
        @self.mcp.resource("resource://qiskit_noise_channels")
        def noise_guide() -> str:
            """Noise modeling with qiskit-aer — NoiseModel construction, fidelity estimation, code examples."""
            return NOISE_GUIDE

    def _register_metal_guide(self):
        @self.mcp.resource("resource://qiskit_metal_components")
        def metal_guide() -> str:
            """Qiskit Metal component catalog and usage notes."""
            return METAL_GUIDE

    def _register_circuit_patterns(self):
        @self.mcp.resource("resource://qiskit_circuit_patterns")
        def circuit_patterns() -> str:
            """Common quantum circuit patterns — Bell, GHZ, QFT, execution, visualization."""
            return CIRCUIT_PATTERNS
