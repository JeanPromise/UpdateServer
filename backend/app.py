import os
from flask import Flask, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename

app = Flask(__name__)

# Configure upload folder
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# Track latest APK info
latest_apk = {
    "filename": None,
    "version": None
}

@app.route("/upload", methods=["POST"])
def upload_file():
    """Admin uploads APK"""
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files["file"]
    version = request.form.get("version", "1.0.0")

    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(filepath)

    # Update latest APK info
    latest_apk["filename"] = filename
    latest_apk["version"] = version

    return jsonify({"message": "File uploaded successfully", "version": version}), 200

@app.route("/latest", methods=["GET"])
def get_latest():
    """Users check for updates"""
    if not latest_apk["filename"]:
        return jsonify({"message": "No update available"}), 404

    return jsonify({
        "filename": latest_apk["filename"],
        "version": latest_apk["version"],
        "download_url": f"/download/{latest_apk['filename']}"
    })

@app.route("/download/<filename>", methods=["GET"])
def download_file(filename):
    """Users download latest APK"""
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename, as_attachment=True)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
