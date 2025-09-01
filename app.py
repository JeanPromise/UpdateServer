from flask import Flask, request, send_from_directory, render_template
from flask_socketio import SocketIO, emit
import os
import datetime

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
socketio = SocketIO(app, cors_allowed_origins="*")

# Store users: just usernames now
connected_users = set()
current_apk = {"filename": None, "version": "1"}

# -----------------------
# Serve admin panel
# -----------------------
@app.route("/")
def index():
    return render_template("index.html", version=current_apk["version"], filename=current_apk["filename"])

# -----------------------
# APK upload
# -----------------------
@app.route("/upload_apk", methods=["POST"])
def upload_apk():
    if "apk_file" not in request.files:
        return "No file uploaded", 400
    f = request.files["apk_file"]
    filename = f.filename
    f.save(os.path.join(app.config["UPLOAD_FOLDER"], filename))
    current_apk["filename"] = filename
    # bump version (simple)
    major, minor, patch = map(int, current_apk["version"].split("."))
    patch += 1
    current_apk["version"] = f"{major}.{minor}.{patch}"

    socketio.emit("apk_update", {"version": current_apk["version"], "filename": filename})
    return f"Uploaded {filename} successfully"

# -----------------------
# WebSocket events
# -----------------------
@socketio.on("connect")
def handle_connect():
    emit("users_list", list(connected_users))
    emit("status", f"APK version: {current_apk['version']}")

@socketio.on("register")
def handle_register(data):
    username = data.get("username", "unknown")
    connected_users.add(username)
    emit("users_list", list(connected_users), broadcast=True)

@socketio.on("logout_all")
def handle_logout_all():
    connected_users.clear()
    emit("users_list", list(connected_users), broadcast=True)

# -----------------------
# Serve uploaded APKs
# -----------------------
@app.route("/apk/<filename>")
def serve_apk(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

# -----------------------
# Run server
# -----------------------
if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
