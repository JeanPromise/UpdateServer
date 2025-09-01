import os
import json
from datetime import datetime
from flask import Flask, request, send_from_directory, jsonify
from flask_socketio import SocketIO, emit

# --- Flask setup ---
app = Flask(__name__, static_folder="../admin_ui", static_url_path="")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

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

    # Broadcast update
    socketio.emit("apk_update", apk_info)
    print(f"[{datetime.utcnow().strftime('%H:%M:%S')}] APK Version updated: v{apk_info['version']}")
    return jsonify(apk_info)

# --- WebSocket events ---
@socketio.on("connect")
def handle_connect():
    # Send current APK info
    emit("apk_update", load_apk_info())
    # Send current users
    emit("users_update", load_users())

@socketio.on("register_device")
def handle_register(data):
    device_id = data.get("device_id")
    version = data.get("version")
    timestamp = datetime.utcnow().isoformat()

    users = load_users()
    # Avoid duplicates
    if not any(u["device_id"] == device_id for u in users):
        user = {
            "device_id": device_id,
            "version": version,
            "connected_at": timestamp
        }
        users.append(user)
        save_users(users)
        emit("user_connected", user, broadcast=True)
        print(f"[{datetime.utcnow().strftime('%H:%M:%S')}] Device connected: {device_id}")

# --- Disconnect event (optional) ---
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
