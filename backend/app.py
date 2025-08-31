from flask import Flask, request, jsonify, send_from_directory
import os

app = Flask(__name__)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

latest_file = None

@app.route("/upload", methods=["POST"])
def upload_apk():
    global latest_file
    if "file" not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files["file"]
    filepath = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(filepath)
    latest_file = file.filename
    return jsonify({"message": f"Uploaded {file.filename} successfully"})

@app.route("/check-update", methods=["GET"])
def check_update():
    if latest_file:
        return jsonify({
            "update": True,
            "file": latest_file,
            "url": f"/download/{latest_file}"
        })
    return jsonify({"update": False})

@app.route("/download/<filename>", methods=["GET"])
def download(filename):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
