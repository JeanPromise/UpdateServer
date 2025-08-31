import os
from flask import Flask, request, jsonify, send_from_directory, render_template
from werkzeug.utils import secure_filename
from datetime import datetime

app = Flask(__name__)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Fake in-memory DB
devices = set()       # unique device ids
users = set()         # admin login names (simulate)
apk_file = None
upload_time = None

@app.route("/")
def home():
    return "Update Server Running ✅"

# Register device install
@app.route("/register_device", methods=["POST"])
def register_device():
    device_id = request.json.get("device_id")
    if device_id:
        devices.add(device_id)
    return jsonify({"status": "ok", "total_devices": len(devices)})

# Upload new APK
@app.route("/upload", methods=["POST"])
def upload_apk():
    global apk_file, upload_time
    if "apk" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["apk"]
    if file.filename == "":
        return jsonify({"error": "Empty filename"}), 400

    filename = secure_filename("app-latest.apk")
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)
    apk_file = filename
    upload_time = datetime.utcnow()
    return jsonify({"message": "APK uploaded successfully", "time": str(upload_time)})

# Download latest APK
@app.route("/download", methods=["GET"])
def download_apk():
    if not apk_file:
        return jsonify({"error": "No APK uploaded yet"}), 404
    return send_from_directory(UPLOAD_FOLDER, apk_file, as_attachment=True)

# Stats for Admin UI
@app.route("/stats", methods=["GET"])
def stats():
    return jsonify({
        "users": len(users) if users else 1,   # assume 1 admin logged in
        "devices": len(devices),
        "apk_uploaded": bool(apk_file),
        "last_upload": str(upload_time) if upload_time else "None"
    })

if __name__ == "__main__":
    app.run(debug=True, port=5000)
