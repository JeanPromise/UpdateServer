from flask import Flask, request, jsonify, send_from_directory
import os, time, hashlib

app = Flask(__name__)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

API_KEY = "290"
users = {}
LATEST_VERSION = "1.0.0"

def hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

@app.route('/')
def index():
    return open("index.html").read()

@app.route('/upload', methods=['POST'])
def upload_file():
    key = request.headers.get("X-API-KEY")
    if key != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401
    file = request.files.get('apk')
    if not file:
        return jsonify({"error": "No file"}), 400

    timestamp = int(time.time())
    filename = f"app-{timestamp}.apk"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)

    # keep only latest
    for f in os.listdir(UPLOAD_FOLDER):
        if f != filename:
            os.remove(os.path.join(UPLOAD_FOLDER, f))

    global LATEST_VERSION
    LATEST_VERSION = f"1.{timestamp}"
    return jsonify({
        "message": "Uploaded",
        "url": f"/uploads/{filename}",
        "version": LATEST_VERSION
    })

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

@app.route('/version')
def version_check():
    files = os.listdir(UPLOAD_FOLDER)
    if not files:
        return jsonify({"latest_version": None, "download_url": None})
    latest = max(files, key=lambda f: os.path.getctime(os.path.join(UPLOAD_FOLDER, f)))
    return jsonify({
        "latest_version": LATEST_VERSION,
        "download_url": f"/uploads/{latest}"
    })

@app.route('/api/register', methods=['POST'])
def api_register():
    data = request.json
    username = data.get("username")
    password = data.get("password")
    if username in users:
        return jsonify({"error": "User exists"}), 400
    users[username] = hash_password(password)
    return jsonify({"message": "Registered"})

@app.route('/api/login', methods=['POST'])
def api_login():
    data = request.json
    username = data.get("username")
    password = data.get("password")
    if users.get(username) == hash_password(password):
        return jsonify({"message": "Login success"})
    return jsonify({"error": "Invalid login"}), 401

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
