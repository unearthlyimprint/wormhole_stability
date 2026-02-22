"""
Tier 1 v3: Trotter Depth Sweep on 3-Qubit Wormhole
=====================================================

WHY v1 AND v2 FAILED:
  v1: Extra qubit pairs were spectators (IonQ optimized them away → F=1.0)
  v2: Multi-qubit extensions broke the circuit (F≈0 even in simulation)

THE CORRECT APPROACH:
  Keep the PROVEN 3-qubit wormhole architecture. Vary circuit depth by
  changing the number of Trotter steps in the bridge Hamiltonian.

  The bridge applies Heisenberg coupling exp(-i H t) between Alice and Bob.
  First-order Trotter decomposition:
  
    exp(-i H t) ≈ [exp(-i XX·t/N) · exp(-i YY·t/N) · exp(-i ZZ·t/N)]^N

  Total evolution is the SAME for all N. But circuit depth scales as:
    CX gates = 4 (entangle + SWAP) + 6·N (bridge Trotter steps)

  N=1:  10 CX gates, depth ~19   (original protocol)
  N=2:  16 CX gates, depth ~31
  N=3:  22 CX gates, depth ~43
  N=5:  34 CX gates, depth ~67
  N=8:  52 CX gates, depth ~103

  In noiseless simulation, Trotter error is small → all give high F.
  On hardware, each additional Trotter step adds real gate noise.
  The SHAPE of the F-vs-depth curve distinguishes CFD from standard noise.

WHAT WE GAIN:
  - All 3 qubits are on the critical path (no spectators)
  - Noiseless baseline is F ≈ 1.0 for all depths
  - Same physical evolution, different circuit depths
  - Directly comparable to the existing F=0.988 hardware data point

Usage:
  python tier1v3_trotter_sweep.py --mode sim --shots 2000
  python tier1v3_trotter_sweep.py --mode hardware --shots 200
"""

import os
import sys
import json
import warnings
import argparse
import numpy as np
from datetime import datetime

warnings.filterwarnings("ignore")

# ============================================================================
# 1. GATE DECOMPOSITIONS (identical to manuscript)
# ============================================================================

def apply_rxx(qc, theta, q1, q2):
    """e^(-i θ XX): H-H-CX-Rz(2θ)-CX-H-H  [2 CX gates]"""
    qc.h(q1); qc.h(q2)
    qc.cx(q1, q2)
    qc.rz(2 * theta, q2)
    qc.cx(q1, q2)
    qc.h(q1); qc.h(q2)

def apply_ryy(qc, theta, q1, q2):
    """e^(-i θ YY): Rx-Rx-CX-Rz(2θ)-CX-Rx†-Rx†  [2 CX gates]"""
    qc.rx(np.pi/2, q1); qc.rx(np.pi/2, q2)
    qc.cx(q1, q2)
    qc.rz(2 * theta, q2)
    qc.cx(q1, q2)
    qc.rx(-np.pi/2, q1); qc.rx(-np.pi/2, q2)

def apply_rzz(qc, theta, q1, q2):
    """e^(-i θ ZZ): CX-Rz(2θ)-CX  [2 CX gates]"""
    qc.cx(q1, q2)
    qc.rz(2 * theta, q2)
    qc.cx(q1, q2)


# ============================================================================
# 2. WORMHOLE BUILDER WITH VARIABLE TROTTER DEPTH
# ============================================================================

def build_wormhole_trotter(n_trotter=1, gamma=0.0, total_coupling=0.785):
    """
    Build the 3-qubit wormhole with variable Trotter depth.
    
    Parameters:
        n_trotter:     Number of Trotter steps (1 = original protocol)
        gamma:         Decoherence parameter
        total_coupling: Total Heisenberg evolution angle (π/4 → π/2 with 2x)
    
    The total Hamiltonian evolution is FIXED at total_coupling.
    More Trotter steps = finer decomposition = more gates = deeper circuit.
    
    CX count: 1 (Bell) + 3 (SWAP) + 6·n_trotter (bridge) = 4 + 6N
    """
    from qiskit import QuantumCircuit, QuantumRegister, ClassicalRegister
    
    alice = QuantumRegister(1, 'A')
    bob = QuantumRegister(1, 'B')
    msg = QuantumRegister(1, 'msg')
    creg = ClassicalRegister(1, 'c')
    qc = QuantumCircuit(alice, bob, msg, creg)
    
    # --- Stage 1: Entanglement (ER bridge) ---
    qc.h(alice[0])
    qc.cx(alice[0], bob[0])
    qc.rz(np.pi, alice[0])
    qc.rz(-np.pi, bob[0])
    
    # --- Stage 2: Message injection ---
    qc.h(msg[0])
    qc.swap(msg[0], alice[0])
    
    # --- Stage 3: CFD Decoherence ---
    if gamma > 0:
        qc.rz(gamma * np.pi * 1.0, alice[0])
        qc.rz(gamma * np.pi * -1.5, bob[0])
    
    # --- Stage 4: Bridge (Trotter-decomposed Heisenberg) ---
    # Split total_coupling into n_trotter equal steps
    step_coupling = total_coupling / n_trotter
    
    for step in range(n_trotter):
        apply_rxx(qc, step_coupling, alice[0], bob[0])
        apply_ryy(qc, step_coupling, alice[0], bob[0])
        apply_rzz(qc, step_coupling, alice[0], bob[0])
    
    # --- Stage 5: Measurement ---
    qc.h(bob[0])
    qc.measure(bob[0], creg[0])
    
    cx_count = 1 + 3 + 6 * n_trotter  # Bell + SWAP + bridge
    
    gate_info = {
        'n_trotter': n_trotter,
        'total_qubits': 3,
        'cx_total': cx_count,
        'step_coupling': round(step_coupling, 6),
        'total_coupling': total_coupling,
    }
    
    return qc, gate_info


# ============================================================================
# 3. FIDELITY COMPUTATION
# ============================================================================

def compute_fidelity(counts, shots):
    success = counts.get('0', 0)
    p_success = success / shots
    fidelity = max(2 * p_success - 1, 0)
    sigma_p = np.sqrt(max(p_success * (1 - p_success), 0) / shots)
    sigma_f = 2 * sigma_p
    return fidelity, sigma_f, p_success


# ============================================================================
# 4. RUN MODES
# ============================================================================

# Trotter step counts to test
TROTTER_STEPS = [1, 2, 3, 5, 8]


def run_local_simulation(shots=2000):
    from qiskit.providers.basic_provider import BasicSimulator
    backend = BasicSimulator()
    results = []
    
    print("=" * 70)
    print("TIER 1 v3: TROTTER DEPTH SWEEP (LOCAL SIMULATION)")
    print(f"Shots: {shots}")
    print("Expected: All steps should give F ≈ 1.0 (Trotter error small)")
    print("=" * 70)
    
    for n_t in TROTTER_STEPS:
        qc, gates = build_wormhole_trotter(n_trotter=n_t, gamma=0.0)
        depth = qc.depth()
        
        job = backend.run(qc, shots=shots)
        counts = job.result().get_counts()
        fidelity, sigma, p_success = compute_fidelity(counts, shots)
        
        result = {
            'n_trotter': n_t,
            'total_qubits': 3,
            'cx_total': gates['cx_total'],
            'circuit_depth': depth,
            'step_coupling': gates['step_coupling'],
            'counts': dict(counts),
            'p_success': round(p_success, 4),
            'fidelity': round(fidelity, 4),
            'sigma': round(sigma, 4),
            'shots': shots,
        }
        results.append(result)
        
        status = "TRAVERSABLE" if fidelity > 0.7 else ("NOISY" if fidelity > 0.2 else "COLLAPSED")
        print(f"\n  Steps={n_t:>2} | {gates['cx_total']:>2} CX | depth {depth:>3} | "
              f"θ_step={gates['step_coupling']:.4f}")
        print(f"  Counts: {dict(counts)}")
        print(f"  F = {fidelity:.4f} ± {sigma:.4f}  |  {status}")
    
    return results


def run_azure(mode='hardware', shots=200):
    from azure.quantum.qiskit import AzureQuantumProvider
    from azure.identity import DeviceCodeCredential
    
    print("1. Authenticating with Azure Quantum...")
    tenant_id = os.environ.get("AZURE_TENANT_ID")
    resource_id = os.environ.get("AZURE_RESOURCE_ID")
    if not tenant_id or not resource_id:
        print("ERROR: Set AZURE_TENANT_ID and AZURE_RESOURCE_ID environment variables")
        print("  export AZURE_TENANT_ID='your-tenant-id'")
        print("  export AZURE_RESOURCE_ID='/subscriptions/.../providers/Microsoft.Quantum/Workspaces/...'")
        sys.exit(1)
    
    print("Authenticating...")
    credential = DeviceCodeCredential(tenant_id=tenant_id)
    provider = AzureQuantumProvider(resource_id=resource_id, location="eastus",
                                     credential=credential)
    
    backend_name = "ionq.simulator" if mode == 'azure_sim' else "ionq.qpu.forte-1"
    backend = provider.get_backend(backend_name)
    print(f"Connected to: {backend.name}")
    
    results = []
    
    print("=" * 70)
    print(f"TIER 1 v3: TROTTER DEPTH SWEEP ON {backend.name.upper()}")
    print(f"Shots: {shots} | 3 qubits | γ = 0.0")
    print(f"Varying Trotter steps: {TROTTER_STEPS}")
    print("=" * 70)
    
    for n_t in TROTTER_STEPS:
        qc, gates = build_wormhole_trotter(n_trotter=n_t, gamma=0.0)
        depth = qc.depth()
        
        print(f"\n--- Steps={n_t} | {gates['cx_total']} CX | depth {depth} ---")
        print(f"    Submitting...")
        
        try:
            job = backend.run(qc, shots=shots)
            res = job.result()
            
            if hasattr(res, 'success') and not res.success:
                print(f"    Failed: {res}")
                continue
            
            counts = res.get_counts()
            fidelity, sigma, p_success = compute_fidelity(counts, shots)
            
            result = {
                'n_trotter': n_t,
                'total_qubits': 3,
                'cx_total': gates['cx_total'],
                'circuit_depth': depth,
                'step_coupling': gates['step_coupling'],
                'counts': dict(counts),
                'p_success': round(p_success, 4),
                'fidelity': round(max(fidelity, 0), 4),
                'sigma': round(sigma, 4),
                'shots': shots,
                'backend': backend.name,
                'timestamp': datetime.now().isoformat(),
            }
            results.append(result)
            
            status = "TRAVERSABLE" if fidelity > 0.7 else ("NOISY" if fidelity > 0.2 else "COLLAPSED")
            print(f"    Counts: {dict(counts)}")
            print(f"    F = {fidelity:.4f} ± {sigma:.4f}  |  {status}")
            
        except Exception as e:
            print(f"    Error: {e}")
            results.append({
                'n_trotter': n_t,
                'cx_total': gates['cx_total'],
                'error': str(e),
            })
    
    return results


# ============================================================================
# 5. SUMMARY AND EXPORT
# ============================================================================

def print_summary(results, mode):
    print("\n" + "=" * 70)
    print("SUMMARY: FIDELITY vs TROTTER DEPTH")
    print("=" * 70)
    print(f"{'Steps':>5} {'CX':>5} {'Depth':>6} "
          f"{'P(0)':>7} {'Fidelity':>10} {'Status':>12}")
    print("-" * 55)
    
    for r in results:
        if 'error' in r:
            print(f"{r['n_trotter']:>5} {r['cx_total']:>5} {'':>6} {'ERROR':>30}")
            continue
        f = r['fidelity']
        status = "TRAVERSABLE" if f > 0.7 else ("NOISY" if f > 0.2 else "COLLAPSED")
        print(f"{r['n_trotter']:>5} {r['cx_total']:>5} {r['circuit_depth']:>6} "
              f"{r['p_success']:>7.4f} {r['fidelity']:>6.4f}±{r['sigma']:.4f} "
              f"{status:>12}")
    
    print("-" * 55)
    
    # Transition analysis
    fids = [r['fidelity'] for r in results if 'fidelity' in r]
    cxs = [r['cx_total'] for r in results if 'fidelity' in r]
    
    if len(fids) >= 3:
        diffs = [fids[i] - fids[i+1] for i in range(len(fids)-1)]
        max_idx = np.argmax(np.abs(diffs))
        print(f"\nLargest drop: {cxs[max_idx]}→{cxs[max_idx+1]} CX  "
              f"ΔF = {diffs[max_idx]:.4f}")
        avg = np.mean(np.abs(diffs))
        if avg > 0:
            ratio = abs(diffs[max_idx]) / avg
            print(f"Sharpness ratio: {ratio:.2f} "
                  f"({'SHARP (supports CFD)' if ratio > 1.5 else 'SMOOTH (standard noise)'})")
    
    # Expected noise model: F ≈ (1-ε)^N_cx where ε is error per CX
    # For Forte-1, ε ≈ 0.003-0.005 per MS gate
    if len(cxs) >= 2 and fids[0] > 0 and fids[-1] > 0:
        try:
            eps = 1 - (fids[-1] / fids[0]) ** (1 / (cxs[-1] - cxs[0]))
            print(f"\nEffective error per CX (exponential model): ε ≈ {eps:.4f}")
            print(f"  (Forte-1 spec: ~0.003-0.005 per MS gate)")
        except:
            pass
    
    # Save
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    fname = f"tier1v3_results_{mode}_{ts}"
    
    with open(f"{fname}.json", 'w') as f:
        json.dump({
            'experiment': 'Tier 1 v3: Trotter Depth Sweep',
            'mode': mode,
            'architecture': '3-qubit wormhole, variable Trotter steps',
            'total_coupling': 0.785,
            'gamma': 0.0,
            'timestamp': datetime.now().isoformat(),
            'note': 'Same total Hamiltonian evolution, split into N Trotter steps. '
                    'More steps = more gates = deeper circuit. Noiseless F ≈ 1.0 for all.',
            'results': results,
        }, f, indent=2)
    
    with open(f"{fname}.csv", 'w') as f:
        f.write("n_trotter,cx_total,circuit_depth,p_success,fidelity,sigma\n")
        for r in results:
            if 'fidelity' in r:
                f.write(f"{r['n_trotter']},{r['cx_total']},{r['circuit_depth']},"
                        f"{r['p_success']},{r['fidelity']},{r['sigma']}\n")
    
    print(f"\nSaved: {fname}.json / .csv")
    return fname


# ============================================================================
# 6. MAIN
# ============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Tier 1 v3: Trotter depth sweep (3-qubit wormhole)"
    )
    parser.add_argument('--mode', choices=['sim', 'azure_sim', 'hardware'],
                        default='sim')
    parser.add_argument('--shots', type=int, default=200)
    args = parser.parse_args()
    
    print(f"\n{'='*70}")
    print(f"TIER 1 v3: TROTTER DEPTH SWEEP")
    print(f"Mode: {args.mode} | Shots: {args.shots}")
    print(f"{'='*70}")
    
    print("\nCircuit configurations (3 qubits, same total evolution):")
    for n_t in TROTTER_STEPS:
        qc, g = build_wormhole_trotter(n_trotter=n_t)
        print(f"  Steps={n_t:>2}: {g['cx_total']:>2} CX gates, "
              f"depth={qc.depth():>3}, θ_step={g['step_coupling']:.4f}")
    
    if args.mode == 'sim':
        results = run_local_simulation(shots=args.shots)
    else:
        results = run_azure(mode=args.mode, shots=args.shots)
    
    print_summary(results, args.mode)
    
    print("\n" + "=" * 70)
    print("INTERPRETATION")
    print("=" * 70)
    print("""
All circuits perform the SAME Hamiltonian evolution on the SAME 3 qubits.
The only difference is how many Trotter steps decompose the bridge.
More steps = more entangling gates = deeper circuit.

On a noiseless simulator, all should give F ≈ 1.0.
On hardware, fidelity will degrade due to gate errors accumulating.

SHARP transition: F stays high up to some critical gate count, then drops.
  → Supports CFD: hardware γ_eff crosses γ_c at that depth.

SMOOTH/EXPONENTIAL decay: F ∝ (1-ε)^N_gates.
  → Standard noise model: each gate contributes independent error.

The effective error rate ε can be compared to IonQ's published specs
(~0.3-0.5% per MS gate) as a consistency check.
""")
