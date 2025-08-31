# backend/app.py
import os
from flask import Flask, send_from_directory, request
from flask_socketio import SocketIO

app = Flask(__name__, static_folder="../admin_ui", static_url_path="/")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Serve index.html
@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")

# Example endpoint to upload APK
@app.route("/upload", methods=["POST"])
def upload_apk():
    if 'file' not in request.files:
        return {"status": "error", "message": "No file provided"}, 400
    file = request.files['file']
    filename = file.filename
    save_path = os.path.join("uploads", filename)
    os.makedirs("uploads", exist_ok=True)
    file.save(save_path)
    # Notify clients about new update
    socketio.emit("new_update", {"filename": filename})
    return {"status": "success", "filename": filename}

# WebSocket event example
@socketio.on("connect")
def handle_connect():
    print(f"Client connected: {request.sid}")
    socketio.emit("message", {"info": "Welcome!"}, to=request.sid)

@socketio.on("disconnect")
def handle_disconnect():
    print(f"Client disconnected: {request.sid}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    socketio.run(app, host="0.0.0.0", port=port)
