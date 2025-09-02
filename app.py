import os
from flask import Flask, request, send_from_directory, render_template

app = Flask(__name__, template_folder=".")  # look for index.html in root

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER


@app.route("/", methods=["GET"])
def index():
    files = os.listdir(app.config["UPLOAD_FOLDER"])
    latest_apk = files[0] if files else None
    return render_template("index.html", latest_apk=latest_apk)


@app.route("/upload", methods=["POST"])
def upload():
    files = os.listdir(app.config["UPLOAD_FOLDER"])

    if "apk" not in request.files:
        return "No file part", 400
    file = request.files["apk"]
    if file.filename == "":
        return "No selected file", 400
    if file and file.filename.endswith(".apk"):
        # delete old APKs
        for f in files:
            os.remove(os.path.join(app.config["UPLOAD_FOLDER"], f))

        filepath = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
        file.save(filepath)
        return f"Uploaded {file.filename} successfully!"

    return "Invalid file type", 400


@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename, as_attachment=True)


@app.route("/healthz")
def healthz():
    return "OK", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
