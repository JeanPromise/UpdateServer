import os
import time
from flask import Flask, request, send_from_directory, jsonify

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# keep track of latest uploaded file
latest_file = None

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)


@app.route("/")
def index():
    global latest_file
    if latest_file:
        return f"<a href='/uploads/{latest_file}'>{latest_file}</a>"
    return "No APK uploaded yet."


@app.route("/upload", methods=["POST"])
def upload_file():
    global latest_file
    if "apk" not in request.files:
        return jsonify({"error": "No file part"}), 400

    file = request.files["apk"]
    if file.filename == "":
        return jsonify({"error": "No selected file"}), 400

    # delete old apk if exists
    if latest_file:
        old_path = os.path.join(app.config["UPLOAD_FOLDER"], latest_file)
        if os.path.exists(old_path):
            os.remove(old_path)

    # generate unique name with timestamp
    timestamp = int(time.time())
    filename = f"app-{timestamp}.apk"
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(filepath)

    latest_file = filename
    return jsonify({"message": "File uploaded", "url": f"/uploads/{filename}"})


@app.route("/uploads/<path:filename>")
def download_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename, as_attachment=True)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
