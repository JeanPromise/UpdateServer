import os
from flask import Flask, request, send_from_directory, jsonify

app = Flask(__name__, static_folder="../admin_ui", static_url_path="")

# Ensure upload folder exists
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# ----------- FRONTEND ROUTES -----------
@app.route("/")
def home():
    """Serve the admin UI (index.html)."""
    return send_from_directory(app.static_folder, "index.html")


@app.route("/<path:filename>")
def static_files(filename):
    """Serve static frontend files like CSS/JS."""
    return send_from_directory(app.static_folder, filename)


# ----------- BACKEND ROUTES -----------
@app.route("/upload", methods=["POST"])
def upload_apk():
    """Admin uploads new APK file."""
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400

    # Save file
    save_path = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(save_path)

    # Write "latest.txt" to track latest APK
    with open(os.path.join(UPLOAD_FOLDER, "latest.txt"), "w") as f:
        f.write(file.filename)

    return jsonify({"message": f"Uploaded {file.filename} successfully"}), 200


@app.route("/latest", methods=["GET"])
def get_latest_apk():
    """Return latest APK filename."""
    latest_file = os.path.join(UPLOAD_FOLDER, "latest.txt")
    if not os.path.exists(latest_file):
        return jsonify({"error": "No APK uploaded yet"}), 404

    with open(latest_file, "r") as f:
        filename = f.read().strip()

    return jsonify({"latest_apk": filename})


@app.route("/download", methods=["GET"])
def download_apk():
    """Download the latest APK file."""
    latest_file = os.path.join(UPLOAD_FOLDER, "latest.txt")
    if not os.path.exists(latest_file):
        return jsonify({"error": "No APK uploaded yet"}), 404

    with open(latest_file, "r") as f:
        filename = f.read().strip()

    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)


# ----------- DEV MODE ENTRY POINT -----------
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
