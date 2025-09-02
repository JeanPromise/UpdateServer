import os
from flask import Flask, request, send_from_directory, render_template_string

app = Flask(__name__)

# Folder to store uploaded APKs
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# Homepage: upload form + latest APK link
@app.route("/", methods=["GET", "POST"])
def index():
    latest_apk = None
    files = sorted(os.listdir(app.config["UPLOAD_FOLDER"]), reverse=True)
    if files:
        latest_apk = files[0]

    if request.method == "POST":
        if "apk" not in request.files:
            return "No file part", 400
        file = request.files["apk"]
        if file.filename == "":
            return "No selected file", 400
        if file and file.filename.endswith(".apk"):
            filepath = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
            file.save(filepath)
            return f"Uploaded successfully! <a href='/{app.config['UPLOAD_FOLDER']}/{file.filename}'>Download here</a>"

    html = """
    <h2>Upload APK</h2>
    <form method="post" enctype="multipart/form-data">
      <input type="file" name="apk" accept=".apk" required>
      <input type="submit" value="Upload">
    </form>
    {% if latest_apk %}
      <h3>Latest APK:</h3>
      <a href="/uploads/{{ latest_apk }}">{{ latest_apk }}</a>
    {% else %}
      <p>No APK uploaded yet.</p>
    {% endif %}
    """
    return render_template_string(html, latest_apk=latest_apk)


# Route to serve uploaded files
@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

# Health check
@app.route("/healthz")
def healthz():
    return "OK", 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
