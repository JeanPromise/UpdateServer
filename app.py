import os
from flask import Flask, request, jsonify, send_from_directory, render_template
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = "uploads"
socketio = SocketIO(app, cors_allowed_origins="*")

# Ensure upload folder exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

# Track connected clients
connected_clients = {}  # device_id: sid
registered_users = {}   # device_id: {info}

# APK version tracking
apk_versions = []
latest_apk = None

# --------------------------
# Admin UI
# --------------------------
@app.route("/")
def index():
    return render_template("index.html", latest_apk=latest_apk, users=registered_users)

# --------------------------
# Upload APK
# --------------------------
@app.route("/upload", methods=["POST"])
def upload_apk():
    global latest_apk
    if 'apk' not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files['apk']
    version = request.form.get("version") or f"v{len(apk_versions)+1}"
    filename = f"{version}_{file.filename}"
    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(filepath)

    latest_apk = {"version": version, "filename": filename, "url": f"/downloads/{filename}"}
    apk_versions.append(latest_apk)

    # Notify all connected clients
    for device_id, sid in connected_clients.items():
        socketio.emit("apk_update", {"url": latest_apk["url"], "version": version}, to=sid)

    print(f"[ADMIN] APK Uploaded: {filename}")
    return jsonify({"success": True, "apk": latest_apk})

# --------------------------
# Serve APKs
# --------------------------
@app.route("/downloads/<path:filename>")
def downloads(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# --------------------------
# WebSocket for clients
# --------------------------
@socketio.on("connect")
def on_connect():
    device_id = request.args.get("device_id")
    if device_id:
        connected_clients[device_id] = request.sid
        registered_users[device_id] = {"online": True}
        emit("server_message", {"msg": f"Connected to server"})
        print(f"[WS] Device {device_id} connected")

@socketio.on("disconnect")
def on_disconnect():
    # Remove from connected_clients
    sid = request.sid
    for device_id, csid in list(connected_clients.items()):
        if csid == sid:
            connected_clients.pop(device_id)
            registered_users[device_id]["online"] = False
            print(f"[WS] Device {device_id} disconnected")
            break

# --------------------------
# Run server
# --------------------------
if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
