import os
import json
from flask import Flask, request, send_from_directory, jsonify
from flask_socketio import SocketIO, emit
from datetime import datetime

app = Flask(__name__, static_folder="admin_ui", static_url_path="")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='threading')

# Files for persistence
USERS_FILE = "users.json"
APK_FILE = "apk_info.json"

# Ensure JSON files exist
if not os.path.exists(USERS_FILE):
    with open(USERS_FILE, "w") as f:
        json.dump([], f)

if not os.path.exists(APK_FILE):
    with open(APK_FILE, "w") as f:
        json.dump({"version": 1, "filename": ""}, f)

# Helper functions
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

# Serve admin UI
@app.route("/")
def index():
    return send_from_directory("admin_ui", "index.html")

# Upload APK
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

    # Notify all admins
    socketio.emit("apk_update", apk_info)
    print(f"[{datetime.utcnow().strftime('%H:%M:%S')}] APK Updated: v{apk_info['version']} ({apk_info['filename']})")
    return jsonify(apk_info)

# WebSocket for devices
@socketio.on("connect")
def handle_connect():
    emit("apk_update", load_apk_info())
    print(f"[{datetime.utcnow().strftime('%H:%M:%S')}] Connected to server")

@socketio.on("disconnect")
def handle_disconnect():
    print(f"[{datetime.utcnow().strftime('%H:%M:%S')}] Disconnected from server")

@socketio.on("register_device")
def handle_register(data):
    users = load_users()
    device_id = None
    version = None

    # Support old-style plain string: "device_id:XYZ;version:1.0"
    if isinstance(data, str):
        parts = data.split(";")
        for p in parts:
            if p.startswith("device_id:"):
                device_id = p.replace("device_id:", "").strip()
            elif p.startswith("version:"):
                version = p.replace("version:", "").strip()
    # Support proper JSON
    elif isinstance(data, dict):
        device_id = data.get("device_id")
        version = data.get("version")

    if device_id:
        timestamp = datetime.utcnow().isoformat()
        # Avoid duplicates
        if not any(u["device_id"] == device_id for u in users):
            users.append({
                "device_id": device_id,
                "version": version,
                "connected_at": timestamp
            })
            save_users(users)
        # Notify all admins
        socketio.emit("users_update", users)
        print(f"[{datetime.utcnow().strftime('%H:%M:%S')}] Users list updated ({len(users)} devices)")

# API endpoint for user list
@app.route("/users")
def get_users():
    return jsonify(load_users())

# Run server
if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)
