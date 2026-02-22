"""
Tier 1 Experiment: Circuit-Depth Sweep for CFD Phase Boundary
==============================================================

Purpose: Determine whether fidelity degrades sharply (CFD prediction)
or smoothly (standard decoherence) as circuit depth increases.

Architecture: Holographic wormhole protocol scaled from N=1 to N=4 pairs.
  N=1: 3 qubits,  ~10 entangling gates
  N=2: 5 qubits,  ~17 entangling gates
  N=3: 7 qubits,  ~24 entangling gates
  N=4: 9 qubits,  ~31 entangling gates

All circuits at gamma=0 (no injected noise). The ONLY noise source
is intrinsic hardware decoherence, which scales with circuit depth.

Run modes:
  --mode sim       Local Qiskit simulator (validation: all should give F≈0.92)
  --mode azure_sim IonQ cloud simulator (validation)
  --mode hardware  IonQ Forte-1 QPU (the actual experiment)

Usage:
  python tier1_depth_sweep.py --mode sim
  python tier1_depth_sweep.py --mode hardware --shots 200
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
# 1. GATE DECOMPOSITIONS (identical to manuscript protocol)
# ============================================================================

def apply_rxx(qc, theta, q1, q2):
    """Hamiltonian simulation of e^(-i * theta * XX)
    Decomposes to: H-H-CX-Rz(2θ)-CX-H-H  (2 entangling gates)"""
    qc.h(q1)
    qc.h(q2)
    qc.cx(q1, q2)
    qc.rz(2 * theta, q2)
    qc.cx(q1, q2)
    qc.h(q1)
    qc.h(q2)

def apply_ryy(qc, theta, q1, q2):
    """Hamiltonian simulation of e^(-i * theta * YY)
    Decomposes to: Rx-Rx-CX-Rz(2θ)-CX-Rx†-Rx†  (2 entangling gates)"""
    qc.rx(np.pi/2, q1)
    qc.rx(np.pi/2, q2)
    qc.cx(q1, q2)
    qc.rz(2 * theta, q2)
    qc.cx(q1, q2)
    qc.rx(-np.pi/2, q1)
    qc.rx(-np.pi/2, q2)

def apply_rzz(qc, theta, q1, q2):
    """Hamiltonian simulation of e^(-i * theta * ZZ)
    Decomposes to: CX-Rz(2θ)-CX  (2 entangling gates)"""
    qc.cx(q1, q2)
    qc.rz(2 * theta, q2)
    qc.cx(q1, q2)


# ============================================================================
# 2. SCALABLE WORMHOLE BUILDER
# ============================================================================

def build_wormhole_scaled(n_pairs, gamma=0.0, coupling=0.785):
    """
    Build the holographic wormhole protocol with variable register size.
    
    Parameters:
        n_pairs: Number of Alice-Bob entangled pairs (1 to 4)
                 Total qubits = 2*n_pairs + 1
        gamma:   Decoherence parameter (0.0 for depth sweep)
        coupling: Bridge coupling strength (π/4, giving π/2 with 2x multiplier)
    
    Returns:
        qc: QuantumCircuit
        gate_counts: dict with entangling gate breakdown
    """
    from qiskit import QuantumCircuit, QuantumRegister, ClassicalRegister
    
    reg_A = QuantumRegister(n_pairs, 'A')
    reg_B = QuantumRegister(n_pairs, 'B')
    reg_msg = QuantumRegister(1, 'msg')
    creg = ClassicalRegister(1, 'c')
    qc = QuantumCircuit(reg_A, reg_B, reg_msg, creg)
    
    # --- Stage 1: Boundary Entanglement (Throat) ---
    # Each pair gets H + CNOT + phase kicks
    # Entangling gates: n_pairs CNOTs
    for i in range(n_pairs):
        qc.h(reg_A[i])
        qc.cx(reg_A[i], reg_B[i])
        qc.rz(np.pi / (i + 1), reg_A[i])
        qc.rz(-np.pi / (i + 1), reg_B[i])
    
    # --- Stage 2: Message Injection ---
    # SWAP decomposes to 3 CNOTs
    qc.h(reg_msg[0])
    qc.swap(reg_msg[0], reg_A[0])
    
    # --- Stage 3: CFD Decoherence (gamma=0 for depth sweep) ---
    if gamma > 0:
        noise_pattern = [1.0, -0.8, 0.5, -1.2]
        for i in range(n_pairs):
            angle = gamma * np.pi * noise_pattern[i]
            qc.rz(angle, reg_A[i])
            qc.rz(-angle * 1.5, reg_B[i])
    
    # --- Stage 4: ER Bridge (Heisenberg coupling) ---
    # Each pair: RXX + RYY + RZZ = 6 CNOTs
    for i in range(n_pairs):
        apply_rxx(qc, coupling, reg_A[i], reg_B[i])
        apply_ryy(qc, coupling, reg_A[i], reg_B[i])
        apply_rzz(qc, coupling, reg_A[i], reg_B[i])
    
    # --- Stage 5: Verification ---
    qc.h(reg_B[0])
    qc.measure(reg_B[0], creg[0])
    
    # Count entangling gates
    n_cx_entangle = n_pairs      # Stage 1
    n_cx_swap = 3                 # Stage 2 (SWAP = 3 CX)
    n_cx_bridge = 6 * n_pairs    # Stage 4 (2 CX per RXX/RYY/RZZ × n_pairs)
    n_cx_total = n_cx_entangle + n_cx_swap + n_cx_bridge
    
    gate_counts = {
        'n_pairs': n_pairs,
        'total_qubits': 2 * n_pairs + 1,
        'cx_entanglement': n_cx_entangle,
        'cx_swap': n_cx_swap,
        'cx_bridge': n_cx_bridge,
        'cx_total': n_cx_total,
    }
    
    return qc, gate_counts


# ============================================================================
# 3. ANALYSIS FUNCTIONS
# ============================================================================

def compute_fidelity(counts, shots):
    """
    Compute teleportation fidelity from measurement counts.
    F = 2*P(success) - 1, where success = measuring |0⟩ at Bob.
    Maps P=0.5 (random) → F=0, P=1.0 (perfect) → F=1.
    """
    success = counts.get('0', 0)
    p_success = success / shots
    fidelity = 2 * p_success - 1
    
    # Statistical uncertainty (binomial)
    p = p_success
    sigma_p = np.sqrt(p * (1 - p) / shots)
    sigma_f = 2 * sigma_p
    
    return fidelity, sigma_f, p_success


def estimate_circuit_depth(qc):
    """Estimate circuit depth from the quantum circuit."""
    return qc.depth()


# ============================================================================
# 4. SIMULATION MODE (Local validation)
# ============================================================================

def run_local_simulation(shots=1000):
    """
    Run all circuit sizes on local Qiskit simulator.
    All should give F ≈ 0.92 since there's no hardware noise.
    This validates the circuits before spending hardware credits.
    """
    from qiskit.providers.basic_provider import BasicSimulator
    backend = BasicSimulator()
    results = []
    
    print("=" * 70)
    print("TIER 1: LOCAL SIMULATION VALIDATION")
    print(f"Shots per circuit: {shots}")
    print("Expected: All circuits should yield F ≈ 0.92 (no hardware noise)")
    print("=" * 70)
    
    for n_pairs in [1, 2, 3, 4]:
        qc, gates = build_wormhole_scaled(n_pairs, gamma=0.0)
        depth = qc.depth()
        
        job = backend.run(qc, shots=shots)
        counts = job.result().get_counts()
        fidelity, sigma, p_success = compute_fidelity(counts, shots)
        
        result = {
            'n_pairs': n_pairs,
            'total_qubits': gates['total_qubits'],
            'cx_total': gates['cx_total'],
            'circuit_depth': depth,
            'counts': dict(counts),
            'p_success': round(p_success, 4),
            'fidelity': round(fidelity, 4),
            'sigma': round(sigma, 4),
            'shots': shots,
        }
        results.append(result)
        
        status = "TRAVERSABLE" if fidelity > 0.7 else ("NOISY" if fidelity > 0.2 else "COLLAPSED")
        print(f"\n  N={n_pairs} pairs | {gates['total_qubits']} qubits | "
              f"{gates['cx_total']} CX gates | depth {depth}")
        print(f"  Counts: {dict(counts)}")
        print(f"  F = {fidelity:.4f} ± {sigma:.4f}  |  P(0) = {p_success:.4f}  |  {status}")
    
    return results


# ============================================================================
# 5. AZURE MODES (Simulator + Hardware)
# ============================================================================

def run_azure(mode='azure_sim', shots=200):
    """
    Run depth sweep on Azure Quantum.
    
    mode='azure_sim':  IonQ cloud simulator (no noise, validation)
    mode='hardware':   IonQ Forte-1 QPU (the actual experiment)
    """
    from azure.quantum.qiskit import AzureQuantumProvider
    from azure.identity import DeviceCodeCredential
    
    # --- Authentication ---
    print("1. Authenticating with Azure Quantum...")
    tenant_id = os.environ.get("AZURE_TENANT_ID")
    resource_id = os.environ.get("AZURE_RESOURCE_ID")
    
    if not tenant_id or not resource_id:
        print("ERROR: Set AZURE_TENANT_ID and AZURE_RESOURCE_ID environment variables")
        print("  export AZURE_TENANT_ID='your-tenant-id'")
        print("  export AZURE_RESOURCE_ID='/subscriptions/.../providers/Microsoft.Quantum/Workspaces/...'")
        sys.exit(1)
    
    print("Authenticating with Azure Quantum...")
    credential = DeviceCodeCredential(tenant_id=tenant_id)
    provider = AzureQuantumProvider(
        resource_id=resource_id, 
        location="eastus", 
        credential=credential
    )
    
    if mode == 'azure_sim':
        backend_name = "ionq.simulator"
    else:
        backend_name = "ionq.qpu.forte-1"
    
    backend = provider.get_backend(backend_name)
    print(f"Connected to: {backend.name}")
    
    # --- Run sweep ---
    results = []
    
    print("=" * 70)
    print(f"TIER 1: CIRCUIT-DEPTH SWEEP ON {backend.name.upper()}")
    print(f"Shots per circuit: {shots}")
    print(f"Gamma: 0.0 (no injected noise — hardware noise only)")
    print(f"Purpose: Map fidelity vs circuit depth to detect phase boundary")
    print("=" * 70)
    
    for n_pairs in [1, 2, 3, 4]:
        qc, gates = build_wormhole_scaled(n_pairs, gamma=0.0)
        depth = qc.depth()
        
        print(f"\n--- N={n_pairs} pairs | {gates['total_qubits']} qubits | "
              f"{gates['cx_total']} CX gates | depth {depth} ---")
        print(f"    Submitting to {backend.name}...")
        
        try:
            job = backend.run(qc, shots=shots)
            res = job.result()
            
            if hasattr(res, 'success') and not res.success:
                print(f"    Job failed: {res}")
                continue
            
            counts = res.get_counts()
            fidelity, sigma, p_success = compute_fidelity(counts, shots)
            
            result = {
                'n_pairs': n_pairs,
                'total_qubits': gates['total_qubits'],
                'cx_total': gates['cx_total'],
                'circuit_depth': depth,
                'counts': dict(counts),
                'p_success': round(p_success, 4),
                'fidelity': round(max(fidelity, 0), 4),  # Clip at 0
                'sigma': round(sigma, 4),
                'shots': shots,
                'backend': backend.name,
                'timestamp': datetime.now().isoformat(),
            }
            results.append(result)
            
            status = "TRAVERSABLE" if fidelity > 0.7 else ("NOISY" if fidelity > 0.2 else "COLLAPSED")
            print(f"    Counts: {dict(counts)}")
            print(f"    F = {fidelity:.4f} ± {sigma:.4f}  |  P(0) = {p_success:.4f}  |  {status}")
            
        except Exception as e:
            print(f"    Error: {e}")
            results.append({
                'n_pairs': n_pairs,
                'total_qubits': gates['total_qubits'],
                'cx_total': gates['cx_total'],
                'error': str(e),
            })
    
    return results


# ============================================================================
# 6. RESULTS SUMMARY AND EXPORT
# ============================================================================

def print_summary(results, mode):
    """Print final summary table and save to file."""
    
    print("\n" + "=" * 70)
    print("SUMMARY: FIDELITY vs CIRCUIT DEPTH")
    print("=" * 70)
    print(f"{'N_pairs':>7} {'Qubits':>6} {'CX_gates':>8} {'Depth':>6} "
          f"{'P(0)':>7} {'Fidelity':>10} {'Status':>12}")
    print("-" * 70)
    
    for r in results:
        if 'error' in r:
            print(f"{r['n_pairs']:>7} {r['total_qubits']:>6} {r['cx_total']:>8} "
                  f"{'':>6} {'':>7} {'ERROR':>10} {'':>12}")
            continue
            
        f = r['fidelity']
        status = "TRAVERSABLE" if f > 0.7 else ("NOISY" if f > 0.2 else "COLLAPSED")
        print(f"{r['n_pairs']:>7} {r['total_qubits']:>6} {r['cx_total']:>8} "
              f"{r['circuit_depth']:>6} {r['p_success']:>7.4f} "
              f"{r['fidelity']:>6.4f}±{r['sigma']:.4f} {status:>12}")
    
    print("-" * 70)
    
    # --- Key question ---
    fidelities = [r['fidelity'] for r in results if 'fidelity' in r]
    if len(fidelities) >= 3:
        # Check for sharp vs smooth transition
        diffs = [fidelities[i] - fidelities[i+1] for i in range(len(fidelities)-1)]
        max_drop_idx = np.argmax(np.abs(diffs))
        
        print(f"\nLargest fidelity drop: between N={results[max_drop_idx]['n_pairs']} "
              f"and N={results[max_drop_idx+1]['n_pairs']} pairs")
        print(f"  ΔF = {diffs[max_drop_idx]:.4f}")
        
        # Rough sharpness metric: ratio of max drop to average drop
        avg_drop = np.mean(np.abs(diffs))
        if avg_drop > 0:
            sharpness = abs(diffs[max_drop_idx]) / avg_drop
            print(f"  Sharpness ratio: {sharpness:.2f} "
                  f"({'SHARP (CFD-like)' if sharpness > 1.5 else 'SMOOTH (standard decoherence)'})")
    
    # --- Save results ---
    filename = f"tier1_results_{mode}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(filename, 'w') as f:
        json.dump({
            'experiment': 'Tier 1: Circuit-Depth Sweep',
            'mode': mode,
            'gamma': 0.0,
            'timestamp': datetime.now().isoformat(),
            'results': results,
        }, f, indent=2)
    print(f"\nResults saved to: {filename}")
    
    # --- Save CSV for plotting ---
    csv_name = filename.replace('.json', '.csv')
    with open(csv_name, 'w') as f:
        f.write("n_pairs,total_qubits,cx_total,circuit_depth,p_success,fidelity,sigma\n")
        for r in results:
            if 'fidelity' in r:
                f.write(f"{r['n_pairs']},{r['total_qubits']},{r['cx_total']},"
                        f"{r['circuit_depth']},{r['p_success']},{r['fidelity']},{r['sigma']}\n")
    print(f"CSV saved to: {csv_name}")
    
    return filename


# ============================================================================
# 7. MAIN
# ============================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Tier 1: Circuit-Depth Sweep for CFD Phase Boundary"
    )
    parser.add_argument('--mode', choices=['sim', 'azure_sim', 'hardware'],
                        default='sim', help='Execution mode')
    parser.add_argument('--shots', type=int, default=200,
                        help='Shots per circuit (default: 200)')
    
    args = parser.parse_args()
    
    print(f"\n{'='*70}")
    print(f"TIER 1 EXPERIMENT: CIRCUIT-DEPTH SWEEP")
    print(f"Mode: {args.mode} | Shots: {args.shots}")
    print(f"Date: {datetime.now().isoformat()}")
    print(f"{'='*70}")
    
    # --- Circuit summary ---
    print("\nCircuit configurations:")
    for n in [1, 2, 3, 4]:
        qc, g = build_wormhole_scaled(n)
        print(f"  N={n}: {g['total_qubits']} qubits, "
              f"{g['cx_total']} CX gates "
              f"({g['cx_entanglement']} entangle + {g['cx_swap']} swap + "
              f"{g['cx_bridge']} bridge), depth={qc.depth()}")
    
    # --- Run ---
    if args.mode == 'sim':
        results = run_local_simulation(shots=args.shots)
    else:
        results = run_azure(mode=args.mode, shots=args.shots)
    
    # --- Summary ---
    outfile = print_summary(results, args.mode)
    
    print("\n" + "=" * 70)
    print("INTERPRETATION GUIDE")
    print("=" * 70)
    print("""
If fidelity drops SHARPLY between two adjacent N values:
  → Consistent with CFD phase boundary (γ_eff crosses γ_c)
  → The transition point identifies the hardware's effective γ_c

If fidelity drops SMOOTHLY and linearly with CX gate count:
  → Standard exponential decoherence (no phase boundary)
  → Each gate contributes independent noise: F ≈ F_gate^(N_gates)

If fidelity drops EXPONENTIALLY with gate count:
  → Standard depolarizing channel behavior
  → F ∝ exp(-N_gates / N_characteristic)

Key: The shape of the curve distinguishes CFD from standard noise models.
""")
