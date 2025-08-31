# backend/app.py
import os
from flask import Flask, request, send_from_directory, jsonify
from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__, static_folder="../admin-ui", static_url_path="")
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 100 * 1024 * 1024  # 100 MB limit

# SocketIO, using default async (eventlet/gevent optional)
socketio = SocketIO(app, cors_allowed_origins="*")

# Serve the admin UI
@app.route("/")
def index():
    return app.send_static_file("index.html")

# Endpoint to upload new APK
@app.route("/upload", methods=["POST"])
def upload_apk():
    if "apk" not in request.files:
        return jsonify({"error": "No APK file"}), 400
    file = request.files["apk"]
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400
    filename = secure_filename(file.filename)
    save_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(save_path)

    # Notify connected clients that a new update is available
    socketio.emit("new_update", {"filename": filename})

    return jsonify({"success": True, "filename": filename})

# Endpoint for clients to download APK
@app.route("/apk/<filename>")
def download_apk(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

# Example WebSocket event
@socketio.on("connect")
def handle_connect():
    print("Client connected")
    emit("message", {"msg": "Connected to update server"})

@socketio.on("disconnect")
def handle_disconnect():
    print("Client disconnected")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    socketio.run(app, host="0.0.0.0", port=port)
