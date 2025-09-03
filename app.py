from flask import Flask, request, jsonify, send_from_directory
import os
import time

app = Flask(__name__)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# 🚨 Change this to your own long random string!
SECRET_KEY = "290"

@app.route('/upload', methods=['POST'])
def upload_file():
    # 🔑 Check API Key
    key = request.headers.get("X-API-KEY")
    if key != SECRET_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    # ✅ Validate file
    if 'apk' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['apk']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    # 🕒 Save with timestamp (unique filename)
    timestamp = int(time.time())
    filename = f"app-{timestamp}.apk"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)

    # 🧹 Delete old files, keep only the latest
    for f in os.listdir(UPLOAD_FOLDER):
        if f != filename:
            os.remove(os.path.join(UPLOAD_FOLDER, f))

    url = f"/uploads/{filename}"
    return jsonify({"message": "File uploaded", "url": url})

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/')
def index():
    files = os.listdir(UPLOAD_FOLDER)
    if not files:
        return "No APK uploaded yet."
    latest = max(files, key=lambda f: os.path.getctime(os.path.join(UPLOAD_FOLDER, f)))
    return f'<a href="/uploads/{latest}">Download Latest APK</a>'
