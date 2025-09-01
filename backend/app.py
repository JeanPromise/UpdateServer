import os
import json
from datetime import datetime
from flask import Flask, request, send_from_directory, jsonify
from flask_socketio import SocketIO, emit

# --- New imports for raw WS ---
from flask_sock import Sock

# --- Flask setup ---
app = Flask(__name__, static_folder="../admin_ui", static_url_path="")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
sock = Sock(app)  # raw WebSocket for APK clients

# --- Persistent storage files ---
USERS_FILE = "users.json"
APK_FILE = "apk_info.json"

# Ensure JSON files exist
if not os.path.exists(USERS_FILE):
    with open(USERS_FILE, "w") as f:
        json.dump([], f)

if not os.path.exists(APK_FILE):
    with open(APK_FILE, "w") as f:
        json.dump({"version": 1, "filename": ""}, f)

# --- Helper functions ---
def load_users():
    with open(USERS_FILE, "r") as f:
        return json.load(f)

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)

def load_apk_info():
    with open(APK_FILE, "r") as f:
        return json.load(f)

def save_apk_info(apk_info):
    with open(APK_FILE, "w") as f:
        json.dump(apk_info, f, indent=2)

# --- Serve admin dashboard ---
@app.route("/")
def index():
    return send_from_directory("../admin_ui", "index.html")

# --- Upload APK endpoint ---
@app.route("/upload_apk", methods=["POST"])
def upload_apk():
    if "apk" not in request.files:
        return "No APK file uploaded", 400

    apk = request.files["apk"]
    apk_info = load_apk_info()
    apk_info["version"] += 1
    apk_info["filename"] = apk.filename

    os.makedirs("uploads", exist_ok=True)
    apk.save(os.path.join("uploads", apk.filename))
    save_apk_info(apk_info)

    # Broadcast to admin panel
    socketio.emit("apk_update", apk_info)

    # Broadcast to all raw WS clients
    for ws in list(connected_ws):
        try:
            ws.send(f"apk:/uploads/{apk.filename}")
        except Exception:
            connected_ws.remove(ws)

    print(f"[{datetime.utcnow().strftime('%H:%M:%S')}] APK updated: v{apk_info['version']}")
    return jsonify(apk_info)

# --- Raw WebSocket for APK clients ---
connected_ws = []

@sock.route("/ws")
def ws_route(ws):
    connected_ws.append(ws)
    try:
        while True:
            data = ws.receive()
            if not data:
                break

            # Device info expected as: device_id:xxx;version:1.0.0
            if data.startswith("device_id:"):
                parts = dict(
                    item.split(":") for item in data.split(";") if ":" in item
                )
                device_id = parts.get("device_id", "unknown")
                version = parts.get("version", "?")
                timestamp = datetime.utcnow().isoformat()

                users = load_users()
                if not any(u["device_id"] == device_id for u in users):
                    user = {
                        "device_id": device_id,
                        "version": version,
                        "connected_at": timestamp,
                    }
                    users.append(user)
                    save_users(users)
                    socketio.emit("user_connected", user, broadcast=True)
                    print(f"[{datetime.utcnow().strftime('%H:%M:%S')}] Device connected: {device_id}")

            elif data == "info":
                ws.send(f"apk:/uploads/{load_apk_info().get('filename','')}")

    except Exception as e:
        print("WS error:", e)
    finally:
        if ws in connected_ws:
            connected_ws.remove(ws)
        print("WS client disconnected")

# --- WebSocket (Socket.IO) for admin ---
@socketio.on("connect")
def handle_connect():
    emit("apk_update", load_apk_info())
    emit("users_update", load_users())

@socketio.on("register_device")
def handle_register(data):
    device_id = data.get("device_id")
    version = data.get("version")
    timestamp = datetime.utcnow().isoformat()

    users = load_users()
    if not any(u["device_id"] == device_id for u in users):
        user = {"device_id": device_id, "version": version, "connected_at": timestamp}
        users.append(user)
        save_users(users)
        emit("user_connected", user, broadcast=True)
        print(f"[{datetime.utcnow().strftime('%H:%M:%S')}] Device connected: {device_id}")

@socketio.on("disconnect")
def handle_disconnect():
    print(f"[{datetime.utcnow().strftime('%H:%M:%S')}] Device disconnected")

# --- API endpoint to fetch users ---
@app.route("/users")
def get_users():
    return jsonify(load_users())

# --- Run server ---
if __name__ == "__main__":
    os.makedirs("uploads", exist_ok=True)
    print("==> Server live at http://0.0.0.0:5000")
    socketio.run(app, host="0.0.0.0", port=5000)
