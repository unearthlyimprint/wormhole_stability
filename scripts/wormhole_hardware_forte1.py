"""
Wormhole Traversability: Hardware Experiment
=============================================
Target: IonQ Forte-1 via Azure Quantum
Protocol: Standard quantum teleportation with classical post-processing
Qubits: 3 (Message, Alice, Bob)
Shots: 1000 per message state

This is a MINIMAL correct experiment. It sends |0> and |1> through
the entanglement bridge and measures whether the information survives
hardware noise.

IMPORTANT: This circuit is intentionally simple (3 qubits, depth 5).
On Forte-1 with ~99.5% two-qubit gate fidelity, we expect:
  F ~ 0.95-0.98 for this shallow circuit
  (well above the classical bound of 0.667)

This establishes the BASELINE fidelity before adding QEC overhead.
"""

import numpy as np
from qiskit import QuantumCircuit, QuantumRegister, ClassicalRegister, transpile
from azure.quantum.qiskit import AzureQuantumProvider
from azure.identity import DeviceCodeCredential

# ============================================================================
# 1. AUTHENTICATE
# ============================================================================
print("=" * 60)
print("WORMHOLE TRAVERSABILITY: HARDWARE EXPERIMENT")
print("=" * 60)

tenant_id = os.environ.get("AZURE_TENANT_ID")
resource_id = os.environ.get("AZURE_RESOURCE_ID")
if not tenant_id or not resource_id:
    print("ERROR: Set AZURE_TENANT_ID and AZURE_RESOURCE_ID environment variables")
    print("  export AZURE_TENANT_ID='your-tenant-id'")
    print("  export AZURE_RESOURCE_ID='/subscriptions/.../providers/Microsoft.Quantum/Workspaces/...'")
    sys.exit(1)

credential = DeviceCodeCredential(tenant_id=tenant_id)
provider = AzureQuantumProvider(resource_id=resource_id, location="eastus", credential=credential)

# Use ionq.qpu.forte-1 (the correct backend name)
backend = provider.get_backend("ionq.qpu.forte-1")
print(f"Backend: {backend.name}")

SHOTS = 1000

# ============================================================================
# 2. BUILD CIRCUITS
# ============================================================================
def build_teleport(message='0'):
    """3-qubit teleportation: q0=msg, q1=Alice, q2=Bob"""
    qr = QuantumRegister(3, 'q')
    cr = ClassicalRegister(3, 'c')
    qc = QuantumCircuit(qr, cr)
    
    # Prepare message
    if message == '1':
        qc.x(qr[0])
    
    # Bell pair (ER bridge)
    qc.h(qr[1])
    qc.cx(qr[1], qr[2])
    
    # Bell measurement
    qc.cx(qr[0], qr[1])
    qc.h(qr[0])
    
    # Measure all
    qc.measure(qr[0], cr[0])
    qc.measure(qr[1], cr[1])
    qc.measure(qr[2], cr[2])
    
    return qc


def compute_fidelity(counts, expected_bob):
    """
    Apply classical teleportation correction:
    If Alice (c[1]) = 1, flip Bob (c[2]).
    Then check if corrected Bob matches expected.
    
    Qiskit bitstring: 'c[2] c[1] c[0]' (MSB first)
    """
    total = sum(counts.values())
    correct = 0
    
    for bitstring, count in counts.items():
        bits = bitstring.replace(' ', '')
        bob_raw = int(bits[0])   # c[2]
        alice = int(bits[1])     # c[1]
        
        bob_corrected = (1 - bob_raw) if alice == 1 else bob_raw
        
        if bob_corrected == expected_bob:
            correct += count
    
    return correct / total


# ============================================================================
# 3. SUBMIT JOBS
# ============================================================================
print("\nSubmitting 2 jobs to IonQ Forte-1...")

# Transpile to native gates (no barriers!)
native_gates = ['cx', 'id', 'rz', 'ry', 'rx', 'h', 'x', 'measure']

# Job A: Send |0>
qc_0 = build_teleport('0')
qc_0t = transpile(qc_0, basis_gates=native_gates)
print(f"\n  Circuit |0>: gates={dict(qc_0t.count_ops())}, depth={qc_0t.depth()}")

# Job B: Send |1>
qc_1 = build_teleport('1')
qc_1t = transpile(qc_1, basis_gates=native_gates)
print(f"  Circuit |1>: gates={dict(qc_1t.count_ops())}, depth={qc_1t.depth()}")

try:
    job_0 = backend.run(qc_0t, shots=SHOTS)
    print(f"\n  Job |0> submitted: {job_0.id()}")
    
    job_1 = backend.run(qc_1t, shots=SHOTS)
    print(f"  Job |1> submitted: {job_1.id()}")
    
    print("\n  Waiting for results (may take 5-15 minutes)...")
    
    # Wait for both results
    result_0 = job_0.result()
    counts_0 = result_0.get_counts()
    
    result_1 = job_1.result()
    counts_1 = result_1.get_counts()
    
    # ============================================================================
    # 4. ANALYSIS
    # ============================================================================
    print("\n" + "=" * 60)
    print("HARDWARE RESULTS")
    print("=" * 60)
    
    # Send |0>
    f_0 = compute_fidelity(counts_0, 0)
    print(f"\n  Send |0>:")
    print(f"    Raw counts: {counts_0}")
    print(f"    Corrected fidelity: {f_0:.4f}")
    
    # Send |1>
    f_1 = compute_fidelity(counts_1, 1)
    print(f"\n  Send |1>:")
    print(f"    Raw counts: {counts_1}")
    print(f"    Corrected fidelity: {f_1:.4f}")
    
    # Average
    f_avg = (f_0 + f_1) / 2
    
    print(f"\n  {'='*40}")
    print(f"  AVERAGE FIDELITY: {f_avg:.4f}")
    print(f"  {'='*40}")
    print(f"  Reference points:")
    print(f"    Perfect:        1.0000")
    print(f"    Classical bound: 0.6667")
    print(f"    Random:          0.5000")
    
    if f_avg > 0.90:
        print(f"\n  VERDICT: HIGH FIDELITY TRAVERSAL")
        print(f"  The ER bridge is operational. Noise is subcritical.")
    elif f_avg > 0.667:
        print(f"\n  VERDICT: QUANTUM CHANNEL OPERATIONAL")
        print(f"  Fidelity exceeds classical bound. Wormhole traversable.")
        print(f"  Noise is present but subcritical (gamma < gamma_c).")
    elif f_avg > 0.55:
        print(f"\n  VERDICT: PARTIAL SIGNAL")
        print(f"  Below classical bound. Quantum advantage lost.")
        print(f"  Device noise approaches critical threshold.")
    else:
        print(f"\n  VERDICT: WORMHOLE COLLAPSED")
        print(f"  Near-random output. Supercritical noise regime.")
        print(f"  Consistent with CFD prediction: gamma_device > gamma_c.")
    
    # Save results
    print(f"\n  Saving results to hardware_results.txt ...")
    with open('hardware_results.txt', 'w') as f:
        f.write("Wormhole Traversability Hardware Results\n")
        f.write(f"Backend: {backend.name}\n")
        f.write(f"Shots: {SHOTS}\n")
        f.write(f"Send |0> counts: {counts_0}\n")
        f.write(f"Send |0> fidelity: {f_0:.4f}\n")
        f.write(f"Send |1> counts: {counts_1}\n")
        f.write(f"Send |1> fidelity: {f_1:.4f}\n")
        f.write(f"Average fidelity: {f_avg:.4f}\n")
    print("  Done.")

except Exception as e:
    print(f"\n  ERROR: {e}")
    print(f"\n  If this is a backend error, try ionq.simulator first.")
