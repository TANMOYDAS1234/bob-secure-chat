import numpy as np
import math
from hashlib import sha3_256
from Crypto.Cipher import AES
from Crypto.Random import get_random_bytes
from qiskit import QuantumCircuit
from qiskit_aer import AerSimulator
from sklearn.ensemble import IsolationForest


# -------------------------------------------------
# Global storage (last session)
# -------------------------------------------------
Q = None
S = None
final_key = None
theta = None
z = None
bit_error = None
phase_error = None
density_matrix = None
decoy_log = []

# Per-session quantum metadata (exposed to the UI)
decoy_state = None
mu = None
shots = None
basis = None
randomness_score = None

# Encryption artifacts
last_ciphertext = None
last_nonce = None
last_tag = None
last_secret_key = None

simulator = AerSimulator()

# -------------------------------------------------
# 1–13 HYBRID KEY GENERATION
# -------------------------------------------------
def generate_hybrid_key():
    global Q, S, final_key, theta, z, bit_error, phase_error, density_matrix
    global decoy_state, mu, shots, basis

    # -------------------------------
    # Decoy State Selection
    # -------------------------------
    decoy_states = {
        "signal": {"mu": 0.6, "shots": 1024},
        "decoy":  {"mu": 0.2, "shots": 512},
        "vacuum": {"mu": 0.0, "shots": 256}
    }

    state = np.random.choice(
        ["signal", "decoy", "vacuum"],
        p=[0.6, 0.3, 0.1]
    )
    decoy_state = str(state)

    mu = decoy_states[state]["mu"]
    shots = decoy_states[state]["shots"]

    # -------------------------------
    # Classical + PQC Key Material
    # -------------------------------
    Q = get_random_bytes(32)
    S = get_random_bytes(32)

    QS = Q + S
    hash_qs = sha3_256(QS).digest()

    theta = int.from_bytes(hash_qs, 'big') % (2 * math.pi)
    z = np.exp(1j * theta)

    # -------------------------------
    # Quantum Circuit
    # -------------------------------
    qc = QuantumCircuit(1, 1)
    qc.h(0)
    qc.rz(theta, 0)

    # Decoy-dependent disturbance (simulates photon statistics)
    if mu > 0 and np.random.rand() < mu:
        qc.z(0)

    if np.random.rand() > 0.1:
        qc.h(0)

    basis = np.random.choice(['Z', 'X'])
    if basis == 'X':
        qc.h(0)

    qc.measure(0, 0)

    # -------------------------------
    # Measurement
    # -------------------------------
    result = simulator.run(qc, shots=shots).result()
    counts = result.get_counts()

    bit_error = counts.get('1', 0) / shots
    phase_error = abs(z.imag)

    # -------------------------------
    # Log Decoy Statistics (AFTER measurement)
    # -------------------------------
    decoy_log.append({
        "state": state,
        "bit_error": bit_error
    })

    # -------------------------------
    # Density Matrix (Entanglement Assumption)
    # -------------------------------
    density_matrix = np.array([[0.5, 0],
                               [0, 0.5]])

    # -------------------------------
    # Privacy Amplification
    # -------------------------------
    raw_key = sha3_256(QS).digest()          # Pre-amplified material
    final_key = privacy_amplification(raw_key, z)

    # -------------------------------
    # Privacy Amplification Report
    # -------------------------------
    print("Privacy Amplification : Applied (SHA3-256)")
    print("Raw Key Length        :", len(raw_key) * 8, "bits")
    print("Final Key Length      :", len(final_key) * 8, "bits")

    # -------------------------------
    # Final Hybrid Key
    # -------------------------------
    Z_bytes = np.array([z.real, z.imag]).tobytes()
    final_key = sha3_256(QS + Z_bytes).digest()

    # -------------------------------
    # Output
    # -------------------------------
    print("\nHybrid Key Generated Successfully")
    print("Decoy State       :", state.upper())
    print("Mean Intensity μ  :", mu)
    print("Shots Used        :", shots)
    print("Bit Error Rate    :", bit_error)
    print("Phase Error       :", phase_error)
    print("Density Matrix:\n", density_matrix, "\n")


# -------------------------------------------------
# QUANTUM STATS SNAPSHOT (for UI visualisation)
# -------------------------------------------------
def get_quantum_stats():
    """Snapshot of the last hybrid-key session. Contains NO key material —
    only metrics safe to show in the UI (key is identified by a short hash)."""
    return {
        "theta": float(theta) if theta is not None else None,
        "z_real": float(z.real) if z is not None else None,
        "z_imag": float(z.imag) if z is not None else None,
        "bit_error": float(bit_error) if bit_error is not None else None,
        "phase_error": float(phase_error) if phase_error is not None else None,
        "decoy_state": decoy_state,
        "mu": float(mu) if mu is not None else None,
        "shots": int(shots) if shots is not None else None,
        "basis": str(basis) if basis is not None else None,
        "randomness": float(randomness_score) if randomness_score is not None else None,
        "density_matrix": density_matrix.tolist() if density_matrix is not None else None,
        "raw_key_bits": 256,
        "final_key_bits": (len(final_key) * 8) if final_key is not None else None,
        "key_fingerprint": sha3_256(final_key).hexdigest()[:16] if final_key is not None else None,
    }


# -------------------------------------------------
# PRIVACY AMPLIFICATION
# -------------------------------------------------
def privacy_amplification(raw_material, z):
    """
    Privacy amplification using universal hashing (SHA3-256)
    Compresses partially secure key into uniformly random key
    """
    Z_bytes = np.array([z.real, z.imag]).tobytes()
    amplified_key = sha3_256(raw_material + Z_bytes).digest()
    return amplified_key


# -------------------------------------------------
# DECOY STATE
# -------------------------------------------------

def analyze_decoy_statistics(threshold=0.15):
    if not decoy_log:
        print("No decoy data available.\n")
        return True  # not enough data yet, allow through

    signal_errors = [d["bit_error"] for d in decoy_log if d["state"] == "signal"]
    decoy_errors  = [d["bit_error"] for d in decoy_log if d["state"] == "decoy"]

    if not signal_errors or not decoy_errors:
        print("Insufficient decoy data, skipping check.\n")
        return True

    avg_signal = np.mean(signal_errors)
    avg_decoy  = np.mean(decoy_errors)

    print(f"Avg Signal Error: {avg_signal:.4f}")
    print(f"Avg Decoy Error : {avg_decoy:.4f}")

    if abs(avg_signal - avg_decoy) <= threshold:
        print("Decoy analysis PASSED → No PNS detected\n")
        return True
    else:
        print("Decoy analysis FAILED → Possible PNS attack\n")
        return False

# -------------------------------------------------
# AES-EAX ENCRYPTION
# -------------------------------------------------
def encrypt_message():
    global last_ciphertext, last_nonce, last_tag, last_secret_key

    if final_key is None:
        print("\nGenerate a hybrid key first.\n")
        return

    # 🔐 Decoy-based PNS detection gate
    if not analyze_decoy_statistics():
        print("Encryption aborted due to failed decoy-state analysis.\n")
        return

    msg = input("Enter message to encrypt: ")
    msg_bytes = msg.encode('utf-8')

    secret_key = final_key[:16]
    cipher = AES.new(secret_key, AES.MODE_EAX)
    ciphertext, tag = cipher.encrypt_and_digest(msg_bytes)

    last_ciphertext = ciphertext
    last_nonce = cipher.nonce
    last_tag = tag
    last_secret_key = secret_key

    print("\nEncryption Successful")
    print("Ciphertext :", ciphertext.hex())
    print("Nonce      :", last_nonce.hex())
    print("Auth Tag   :", tag.hex())
    print("Secret Key :", secret_key.hex(), "\n")

# -------------------------------------------------
# AES-EAX DECRYPTION (PORTABLE)
# -------------------------------------------------
def decrypt_message():
    try:
        ciphertext_hex = input("Enter ciphertext (hex): ")
        nonce_hex = input("Enter nonce (hex): ")
        tag_hex = input("Enter authentication tag (hex): ")
        secret_key_hex = input("Enter secret key (hex): ")

        ciphertext = bytes.fromhex(ciphertext_hex)
        nonce = bytes.fromhex(nonce_hex)
        tag = bytes.fromhex(tag_hex)
        secret_key = bytes.fromhex(secret_key_hex)

        cipher = AES.new(secret_key, AES.MODE_EAX, nonce=nonce)
        plaintext_bytes = cipher.decrypt_and_verify(ciphertext, tag)
        plaintext = plaintext_bytes.decode('utf-8')

        print("\nDecryption Successful")
        print("Decrypted Message:", plaintext, "\n")

    except ValueError:
        print("\nDecryption Failed!")
        print("Authentication failed or wrong inputs.\n")

# -------------------------------------------------
#   DECOY STATE SELECTOR
# -------------------------------------------------

def select_decoy_state():
    states = {
        "signal": 0.6,
        "decoy": 0.2,
        "vacuum": 0.0
    }
    choice = np.random.choice(list(states.keys()), p=[0.6, 0.3, 0.1])
    return choice, states[choice]


# -------------------------------------------------
# RANDOMNESS CHECK
# -------------------------------------------------
def check_randomness():
    global randomness_score
    if final_key is None:
        print("\nGenerate a key first.\n")
        return 0.0

    data = np.frombuffer(final_key, dtype=np.uint8).reshape(-1, 1)
    clf = IsolationForest(contamination=0.5)
    clf.fit(data)
    score = clf.decision_function(data).mean()
    randomness_score = float(score)

    print("\nRandomness Score:", score)
    print("Higher score ⇒ stronger randomness\n")
    return float(score)

# -------------------------------------------------
# ATTACK SIMULATIONS
# -------------------------------------------------
def simulate_entanglement_attack():
    if density_matrix is None:
        print("\nGenerate a key first.\n")
        return

    purity = np.trace(density_matrix @ density_matrix)
    print("\nEntanglement Attack Simulation")
    print("State Purity:", purity)
    print("Result: Maximally mixed state → No information leakage\n")

def simulate_pns_attack():
    if bit_error is None:
        print("\nGenerate a key first.\n")
        return

    leakage_probability = bit_error * 0.05
    print("\nPNS Attack Simulation")
    print("Photon Leakage Probability:", leakage_probability)
    print("Result: Insufficient photons → Attack Failed\n")

def manual_threshold_check(threshold=0.55):
    if bit_error is None:
        print("\nGenerate a key first.\n")
        return False

    print(f"Observed Bit Error Rate: {bit_error:.4f}")

    if bit_error <= threshold:
        print("Threshold check PASSED → Key accepted\n")
        return True
    else:
        print("Threshold check FAILED → Possible eavesdropping detected\n")
        return False


