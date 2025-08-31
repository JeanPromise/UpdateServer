import os
from flask import Flask, send_from_directory, jsonify, request
from flask_socketio import SocketIO

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ADMIN_UI_DIR = os.path.join(BASE_DIR, "..", "admin_ui")  # <- corrected folder name

app = Flask(__name__, static_folder=None)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")  # Use 'threading' for Render

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

# Example API endpoint for uploading APKs
@app.route("/upload", methods=["POST"])
def upload_apk():
    if "apk" not in request.files:
        return jsonify({"error": "No APK file provided"}), 400
    apk_file = request.files["apk"]
    save_path = os.path.join(BASE_DIR, "uploads", apk_file.filename)
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    apk_file.save(save_path)

    # Notify connected clients about the update
    socketio.emit("apk_update", {"filename": apk_file.filename})
    return jsonify({"message": "APK uploaded successfully"}), 200

# SocketIO event example
@socketio.on("connect")
def handle_connect():
    print("Client connected")

@socketio.on("disconnect")
def handle_disconnect():
    print("Client disconnected")

if __name__ == "__main__":
    os.makedirs(os.path.join(BASE_DIR, "uploads"), exist_ok=True)
    socketio.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))
