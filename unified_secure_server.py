import os
import time
import requests
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
from Crypto.Cipher import AES
import protocol_core as protocol
import threading
import uuid
import json

MESSAGE_QUEUE = []
PENDING_MESSAGES = []
MESSAGE_HISTORY = []
TYPING_STATUS = False
REACTIONS = {}          # msg_id -> { user: emoji }  (shared id, set on both nodes)
# ==============================
# CONFIGURATION (ALICE)
# ==============================
PORT      = int(os.environ.get('PORT', 5002))
NODE_NAME = "Bob"
PEER_NAME = "Alice"
PEER_URL  = os.environ.get('PEER_URL', 'http://127.0.0.1:5001/receive')
QC_SERVER = os.environ.get('QC_SERVER', 'http://127.0.0.1:5003')
TRANSFER_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "transfer")
INBOX_DIR    = os.path.join(os.path.dirname(os.path.abspath(__file__)), "inbox")

# ==============================
# APP INIT
# ==============================
app = Flask(__name__)
CORS(app)

# ==============================
# UTILITIES
# ==============================

def ensure_dir():
    os.makedirs(TRANSFER_DIR, exist_ok=True)
    os.makedirs(INBOX_DIR, exist_ok=True)


def send_to_peer(session_id, meta=None, msg_id=None):
    files = {
        'ciphertext': open(f"{TRANSFER_DIR}/ciphertext.bin", 'rb'),
        'nonce': open(f"{TRANSFER_DIR}/nonce.bin", 'rb'),
        'tag': open(f"{TRANSFER_DIR}/tag.bin", 'rb'),
        'key': open(f"{TRANSFER_DIR}/key.bin", 'rb')
    }
    data = {"session": session_id}
    if meta:
        data["meta"] = json.dumps(meta)   # non-key quantum stats for the peer's inspector
    if msg_id:
        data["msg_id"] = msg_id           # shared message id, so reactions match on both sides
    try:
        response = requests.post(PEER_URL, files=files, data=data, timeout=5)
        return response.status_code == 200
    except Exception as e:
        print("[NODE] Transmission Error:", e)
        return False

# ==============================
# UI
# ==============================
@app.route("/")
def serve_ui():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    file_path = os.path.join(base_dir, "ui", "Unified_Secure_Chat.html")
    with open(file_path, "r", encoding="utf-8") as f:
        html = f.read()
    return Response(html, mimetype="text/html")

@app.route("/video")
def serve_video():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    video_path = os.path.join(base_dir, "ui", "bob_loader.mp4")
    file_size = os.path.getsize(video_path)
    range_header = request.headers.get('Range', None)
    if range_header:
        byte_start, byte_end = 0, None
        match = range_header.strip().replace('bytes=', '').split('-')
        byte_start = int(match[0])
        byte_end = int(match[1]) if match[1] else file_size - 1
        length = byte_end - byte_start + 1
        with open(video_path, 'rb') as f:
            f.seek(byte_start)
            data = f.read(length)
        rv = Response(data, status=206, mimetype='video/mp4')
        rv.headers['Content-Range'] = f'bytes {byte_start}-{byte_end}/{file_size}'
        rv.headers['Accept-Ranges'] = 'bytes'
        rv.headers['Content-Length'] = str(length)
        return rv
    def generate():
        with open(video_path, 'rb') as f:
            while True:
                chunk = f.read(65536)
                if not chunk:
                    break
                yield chunk
    rv = Response(generate(), mimetype='video/mp4')
    rv.headers['Accept-Ranges'] = 'bytes'
    rv.headers['Content-Length'] = str(file_size)
    return rv

@app.route('/peer_url')
def peer_url_route():
    return jsonify({'url': PEER_URL.replace('/receive', '')})

@app.route("/node_info", methods=["GET"])
def node_info():
    return jsonify({"name": NODE_NAME})

@app.route("/peer_status", methods=["GET"])
def peer_status():
    try:
        resp = requests.get(PEER_URL.replace('/receive', '/ping'), timeout=2)
        return jsonify({"online": resp.status_code == 200})
    except:
        return jsonify({"online": False})

@app.route("/ping", methods=["GET"])
def ping():
    return jsonify({"status": "ok"})

@app.route("/delivery_status", methods=["GET"])
def delivery_status():
    return jsonify({"pending": len(PENDING_MESSAGES)})

@app.route("/message_history", methods=["GET"])
def message_history():
    return jsonify({"messages": MESSAGE_HISTORY})

def _relay_reaction(path, payload):
    """Relay a reaction to the peer server-side (reliable, like message delivery)."""
    try:
        requests.post(PEER_URL.replace('/receive', path), json=payload, timeout=2)
    except Exception:
        pass

@app.route("/add_reaction", methods=["POST"])
def add_reaction():
    data = request.get_json(force=True)
    msg_id = data.get("msg_id")
    emoji = data.get("emoji")
    user = data.get("user")
    if not (msg_id and emoji and user):
        return jsonify({"status": "error"}), 400
    REACTIONS.setdefault(msg_id, {})[user] = emoji
    if data.get("relay", True):
        _relay_reaction('/add_reaction', {"msg_id": msg_id, "emoji": emoji, "user": user, "relay": False})
    return jsonify({"status": "ok"})

@app.route("/remove_reaction", methods=["POST"])
def remove_reaction():
    data = request.get_json(force=True)
    msg_id = data.get("msg_id")
    user = data.get("user")
    if not (msg_id and user):
        return jsonify({"status": "error"}), 400
    if msg_id in REACTIONS:
        REACTIONS[msg_id].pop(user, None)
        if not REACTIONS[msg_id]:
            REACTIONS.pop(msg_id, None)
    if data.get("relay", True):
        _relay_reaction('/remove_reaction', {"msg_id": msg_id, "user": user, "relay": False})
    return jsonify({"status": "ok"})

@app.route("/get_reactions", methods=["GET"])
def get_reactions():
    return jsonify({"reactions": [{"msg_id": k, "reactions": v} for k, v in list(REACTIONS.items()) if v]})

@app.route("/notify_typing", methods=["POST"])
def notify_typing():
    data = request.get_json()
    typing = data.get("typing", False)
    try:
        requests.post(PEER_URL.replace('/receive', '/set_typing'), json={"typing": typing}, timeout=1)
    except:
        pass
    return jsonify({"status": "ok"})

@app.route("/set_typing", methods=["POST"])
def set_typing():
    global TYPING_STATUS
    data = request.get_json()
    TYPING_STATUS = data.get("typing", False)
    return jsonify({"status": "ok"})

@app.route("/peer_typing", methods=["GET"])
def peer_typing():
    return jsonify({"typing": TYPING_STATUS})

# ==============================
# RECEIVE (from peer)
# ==============================
@app.route("/receive", methods=["POST"])
def receive():
    ensure_dir()
    try:
        msg_id = str(uuid.uuid4())
        msg_dir = os.path.join(INBOX_DIR, msg_id)
        os.makedirs(msg_dir, exist_ok=True)
        for name, file in request.files.items():
            file.save(os.path.join(msg_dir, f"{name}.bin"))

        meta = request.form.get("meta")
        if meta:
            with open(os.path.join(msg_dir, "meta.json"), "w", encoding="utf-8") as f:
                f.write(meta)

        src_id = request.form.get("msg_id")
        if src_id:
            with open(os.path.join(msg_dir, "srcid.txt"), "w", encoding="utf-8") as f:
                f.write(src_id)

        MESSAGE_QUEUE.append(msg_id)
        print(f"[NODE] New encrypted packet queued: {msg_id}")

        session = request.form.get("session")
        if session:
            try:
                requests.post(f"{QC_SERVER}/qc_confirm/{session}", timeout=2)
            except:
                pass

        return jsonify({"status": "RECEIVED"}), 200
    except Exception as e:
        return jsonify({"status": "ERROR", "message": str(e)}), 500

# ==============================
# ENCRYPT & SEND
# ==============================
@app.route("/encrypt", methods=["POST"])
def encrypt():
    try:
        data = request.get_json(force=True)
        message = data.get("message", "").strip()
        if not message:
            return jsonify({"status": "ERROR", "message": "Empty message"}), 400

        ensure_dir()

        # 1. Key generation (UNCHANGED)
        protocol.generate_hybrid_key()
        secret_key = protocol.final_key[:16]

        # 2. Randomness evaluation (UNCHANGED)
        raw_randomness = float(protocol.check_randomness())
        randomness = max(0.0, min(1.0, raw_randomness))

        # 2b. Quantum stats snapshot for the UI inspector (no key material)
        qmeta = protocol.get_quantum_stats()
        qmeta["randomness"] = randomness

        # ---- STEP A : ask QC for session ----
        resp = requests.post(
            f"{QC_SERVER}/qc_request",
            json={
                "theta": float(protocol.theta),
                "randomness": float(randomness),
                "decoy_state": qmeta.get("decoy_state"),
                "sender": NODE_NAME,
                "receiver": PEER_NAME
            },
            timeout=5
        )
        if resp.status_code != 200:
            return jsonify({"status": "REJECTED", "message": "Quantum channel rejected"}), 403
        session = resp.json()["session"]

        # ---- STEP B : wait for receiver readiness ----
        qc_data = None
        for _ in range(10):  # ~5s
            status = requests.get(f"{QC_SERVER}/qc_status/{session}", timeout=5).json()
            if status.get("status") == "TRANSMIT":
                qc_data = status
                break
            time.sleep(0.5)
        if not qc_data:
            return jsonify({"status": "ERROR", "message": "Receiver not ready"}), 408
        qmeta["trust"] = qc_data.get("trust")

        # 3. Encryption (UNCHANGED)
        msg_id = str(uuid.uuid4())
        cipher = AES.new(secret_key, AES.MODE_EAX)
        ciphertext, tag = cipher.encrypt_and_digest(message.encode())

        # 4. Save artifacts
        with open(f"{TRANSFER_DIR}/ciphertext.bin", "wb") as f: f.write(ciphertext)
        with open(f"{TRANSFER_DIR}/nonce.bin", "wb") as f: f.write(cipher.nonce)
        with open(f"{TRANSFER_DIR}/tag.bin", "wb") as f: f.write(tag)
        with open(f"{TRANSFER_DIR}/key.bin", "wb") as f: f.write(secret_key)

        # 5. Send to peer
        delivered = False
        transmission_success = send_to_peer(session, meta=qmeta, msg_id=msg_id)
        if not transmission_success:
            # Queue message for later delivery
            PENDING_MESSAGES.append({
                "msg_id": msg_id,
                "session": session,
                "trust": qc_data.get("trust"),
                "bit_error": qc_data.get("bit_error"),
                "meta": qmeta,
                "timestamp": time.time()
            })
            MESSAGE_HISTORY.append({"id": msg_id, "delivered": False, "text": message})
            return jsonify({
                "status": "SENT",
                "delivered": False,
                "msg_id": msg_id,
                "message": "Queued for delivery",
                "trust": qc_data.get("trust"),
                "quantum": qmeta
            }), 200
        delivered = True

        MESSAGE_HISTORY.append({"id": msg_id, "delivered": delivered, "text": message})
        return jsonify({
            "status": "SUCCESS",
            "delivered": delivered,
            "msg_id": msg_id,
            "message": "Encrypted and delivered",
            "trust": qc_data.get("trust"),
            "bit_error": qc_data.get("bit_error"),
            "quantum": qmeta
        }), 200

    except Exception as e:
        print("[NODE ERROR]", repr(e))
        return jsonify({"status": "ERROR", "message": str(e)}), 500

# ==============================
# DECRYPT
# ==============================
@app.route("/decrypt", methods=["POST"])
def decrypt():
    if not MESSAGE_QUEUE:
        return jsonify({"status": "EMPTY"})

    msg_id = MESSAGE_QUEUE.pop(0)
    msg_dir = os.path.join(INBOX_DIR, msg_id)
    try:
        with open(os.path.join(msg_dir, "ciphertext.bin"), "rb") as f: ciphertext = f.read()
        with open(os.path.join(msg_dir, "nonce.bin"),      "rb") as f: nonce = f.read()
        with open(os.path.join(msg_dir, "tag.bin"),        "rb") as f: tag = f.read()
        with open(os.path.join(msg_dir, "key.bin"),        "rb") as f: secret_key = f.read()

        cipher = AES.new(secret_key, AES.MODE_EAX, nonce=nonce)
        plaintext = cipher.decrypt_and_verify(ciphertext, tag)

        message_text = plaintext.decode()

        quantum = None
        meta_path = os.path.join(msg_dir, "meta.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    quantum = json.loads(f.read())
            except Exception:
                quantum = None

        src_id = None
        srcid_path = os.path.join(msg_dir, "srcid.txt")
        if os.path.exists(srcid_path):
            try:
                with open(srcid_path, "r", encoding="utf-8") as f:
                    src_id = f.read().strip()
            except Exception:
                src_id = None

        MESSAGE_HISTORY.append({"id": src_id, "text": message_text, "delivered": True})
        return jsonify({"status": "SUCCESS", "message": message_text, "quantum": quantum, "msg_id": src_id})

    except FileNotFoundError:
        return jsonify({"status": "EMPTY"})
    except Exception as e:
        return jsonify({"status": "ERROR", "message": str(e)})

# ==============================
# RUN
# ==============================
def heartbeat():
    while True:
        try:
            requests.post(f"{QC_SERVER}/qc_heartbeat", json={"name": NODE_NAME}, timeout=2)
            # Try to send pending messages
            if PENDING_MESSAGES:
                print(f"[NODE] Attempting to deliver {len(PENDING_MESSAGES)} pending messages")
                try:
                    resp = requests.get(PEER_URL.replace('/receive', '/ping'), timeout=2)
                    if resp.status_code == 200:
                        print(f"[NODE] Peer is online, attempting delivery")
                        for msg in PENDING_MESSAGES[:]:
                            print(f"[NODE] Sending msg_id: {msg['msg_id']}")
                            if send_to_peer(msg["session"], meta=msg.get("meta"), msg_id=msg.get("msg_id")):
                                PENDING_MESSAGES.remove(msg)
                                # Update delivery status
                                for history_msg in MESSAGE_HISTORY:
                                    if history_msg["id"] == msg["msg_id"]:
                                        history_msg["delivered"] = True
                                        print(f"[NODE] Updated delivery status for {msg['msg_id']}")
                                        break
                                print(f"[NODE] Queued message delivered")
                            else:
                                print(f"[NODE] Failed to send msg_id: {msg['msg_id']}")
                    else:
                        print(f"[NODE] Peer offline, keeping messages queued")
                except Exception as e:
                    print(f"[NODE] Heartbeat error: {e}")
        except:
            pass
        time.sleep(3)

threading.Thread(target=heartbeat, daemon=True).start()

if __name__ == "__main__":
    print(f"[{NODE_NAME}] Running on http://0.0.0.0:{PORT}")
    app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)