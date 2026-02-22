"""
Trotter Cost Test: Submit Steps=8 (52 CX) with 10 shots to IonQ Forte-1
=========================================================================
Small test to determine AQT consumption for the deepest Trotter circuit 
before committing to a full sweep.

Usage:
    source quantum-env/bin/activate
    python trotter_cost_test.py
"""

import sys
import json
import warnings
import numpy as np
from datetime import datetime

warnings.filterwarnings("ignore")

# Import the proven circuit builder
from tier1v3_trotter_sweep import build_wormhole_trotter, compute_fidelity

TROTTER_STEPS = 8
SHOTS = 10

def main():
    print("=" * 60)
    print("  TROTTER COST TEST")
    print(f"  Steps={TROTTER_STEPS} | {4 + 6*TROTTER_STEPS} CX gates | {SHOTS} shots")
    print(f"  Target: ionq.qpu.forte-1")
    print("=" * 60)
    
    # Build the circuit
    qc, gates = build_wormhole_trotter(n_trotter=TROTTER_STEPS, gamma=0.0)
    print(f"\n  Circuit: {gates['cx_total']} CX gates, depth={qc.depth()}")
    print(f"  Step coupling: {gates['step_coupling']}")
    
    # Authenticate with Azure
    print("\n  Authenticating with Azure Quantum...")
    
    from azure.quantum.qiskit import AzureQuantumProvider
    from azure.identity import DeviceCodeCredential
    
    tenant_id = os.environ.get("AZURE_TENANT_ID")
    resource_id = os.environ.get("AZURE_RESOURCE_ID")
    if not tenant_id or not resource_id:
        print("  ERROR: Set AZURE_TENANT_ID and AZURE_RESOURCE_ID environment variables")
        sys.exit(1)
    
    credential = DeviceCodeCredential(tenant_id=tenant_id)
    provider = AzureQuantumProvider(
        resource_id=resource_id,
        location="eastus",
        credential=credential
    )
    
    backend = provider.get_backend("ionq.qpu.forte-1")
    print(f"  Connected to: {backend.name}")
    
    # Submit
    print(f"\n  Submitting {SHOTS}-shot cost test...")
    print(f"  (This will show real AQT consumption for 52-CX circuit)")
    
    try:
        job = backend.run(qc, shots=SHOTS)
        print(f"  Job ID: {job.job_id()}")
        print(f"  Waiting for completion...")
        
        result = job.result()
        counts = result.get_counts()
        fidelity, sigma, p_success = compute_fidelity(counts, SHOTS)
        
        print(f"\n  Results:")
        print(f"    Counts: {dict(counts)}")
        print(f"    P(0) = {p_success:.4f}")
        print(f"    F = {fidelity:.4f} ± {sigma:.4f}")
        
        # Save
        output = {
            'experiment': 'Trotter Cost Test',
            'n_trotter': TROTTER_STEPS,
            'cx_total': gates['cx_total'],
            'circuit_depth': qc.depth(),
            'shots': SHOTS,
            'counts': dict(counts),
            'fidelity': round(fidelity, 4),
            'sigma': round(sigma, 4),
            'p_success': round(p_success, 4),
            'backend': backend.name,
            'timestamp': datetime.now().isoformat(),
            'note': 'Cost test: check Azure portal for AQT consumption'
        }
        
        fname = f"trotter_cost_test_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        with open(fname, 'w') as f:
            json.dump(output, f, indent=2)
        
        print(f"\n  Saved: {fname}")
        print(f"\n  >>> CHECK AZURE PORTAL for AQT consumption <<<")
        print(f"  >>> Then multiply by (200/10) to estimate 200-shot cost <<<")
        
    except Exception as e:
        print(f"\n  ERROR: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
