import os
import json
from flask import Flask, send_from_directory, jsonify, request
from flask_socketio import SocketIO

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ADMIN_UI_DIR = os.path.join(BASE_DIR, "..", "admin_ui")  # matches your GitHub structure
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
VERSION_FILE = os.path.join(UPLOAD_DIR, "version.json")

app = Flask(__name__, static_folder=None)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

os.makedirs(UPLOAD_DIR, exist_ok=True)

# Initialize versioning
if not os.path.exists(VERSION_FILE):
    with open(VERSION_FILE, "w") as f:
        json.dump({"version": 1}, f)

def increment_version():
    with open(VERSION_FILE, "r") as f:
        data = json.load(f)
    data["version"] += 1
    with open(VERSION_FILE, "w") as f:
        json.dump(data, f)
    return data["version"]

def get_current_version():
    with open(VERSION_FILE, "r") as f:
        data = json.load(f)
    return data["version"]

# Serve the admin UI
@app.route("/")
def serve_index():
    index_path = os.path.join(ADMIN_UI_DIR, "index.html")
    if os.path.exists(index_path):
        return send_from_directory(ADMIN_UI_DIR, "index.html")
    return "Admin UI not found", 404

@app.route("/<path:path>")
def serve_static(path):
    file_path = os.path.join(ADMIN_UI_DIR, path)
    if os.path.exists(file_path):
        return send_from_directory(ADMIN_UI_DIR, path)
    return "File not found", 404

# Upload APK endpoint with automatic version increment
@app.route("/upload", methods=["POST"])
def upload_apk():
    if "apk" not in request.files:
        return jsonify({"error": "No APK file provided"}), 400
    apk_file = request.files["apk"]

    # Increment version automatically
    version = increment_version()
    apk_name = f"{os.path.splitext(apk_file.filename)[0]}_v{version}.apk"
    save_path = os.path.join(UPLOAD_DIR, apk_name)
    apk_file.save(save_path)

    # Notify all connected clients
    socketio.emit("apk_update", {"filename": apk_name, "version": version})
    return jsonify({"message": "APK uploaded", "version": version, "filename": apk_name}), 200

# WebSocket events
@socketio.on("connect")
def handle_connect():
    print("Client connected")
    # send current version on connect
    socketio.emit("apk_update", {"version": get_current_version()})

@socketio.on("disconnect")
def handle_disconnect():
    print("Client disconnected")

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
