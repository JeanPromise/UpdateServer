from flask import Flask, request, jsonify, send_file
import os
import json
import requests
import base64
from datetime import datetime
from io import BytesIO

app = Flask(__name__)

# GitHub config
GITHUB_REPO = "JeanPromise/UpdateServer"
BRANCH = "main"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

USERS_FILE = "users.json"
APK_FILE = "apk.json"


# ---------------- GitHub Helpers ---------------- #
def github_get_file(filename):
    """Fetch file content from GitHub"""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}?ref={BRANCH}"
    r = requests.get(url, headers={"Authorization": f"token {GITHUB_TOKEN}"})
    if r.status_code == 200:
        content = base64.b64decode(r.json()["content"]).decode()
        return json.loads(content)
    else:
        print(f"⚠️ Could not fetch {filename}, using default.")
        if filename == USERS_FILE:
            return []
        if filename == APK_FILE:
            return {"version": None, "filename": None}
        return {}


def github_push_file(filename, content):
    """Push file content back to GitHub"""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{filename}"
    # Get current file SHA
    r = requests.get(url, headers={"Authorization": f"token {GITHUB_TOKEN}"})
    sha = r.json()["sha"] if r.status_code == 200 else None

    encoded = base64.b64encode(content.encode()).decode()
    data = {
        "message": f"Update {filename} at {datetime.utcnow()}",
        "content": encoded,
        "branch": BRANCH,
    }
    if sha:
        data["sha"] = sha

    res = requests.put(url, headers={"Authorization": f"token {GITHUB_TOKEN}"}, json=data)
    if res.status_code in [200, 201]:
        print(f"✅ {filename} pushed to GitHub")
    else:
        print(f"❌ Failed to push {filename}: {res.text}")


# ---------------- Data Load ---------------- #
users = github_get_file(USERS_FILE)
apk_data = github_get_file(APK_FILE)


# ---------------- Routes ---------------- #
@app.route("/register", methods=["POST"])
def register():
    data = request.json
    username = data.get("username")
    password = data.get("password")

    if any(u["username"] == username for u in users):
        return jsonify({"error": "User already exists"}), 400

    users.append({"username": username, "password": password})

    # Save to GitHub
    github_push_file(USERS_FILE, json.dumps(users, indent=2))

    return jsonify({"message": "User registered"})


@app.route("/login", methods=["POST"])
def login():
    data = request.json
    username = data.get("username")
    password = data.get("password")

    for u in users:
        if u["username"] == username and u["password"] == password:
            return jsonify({"message": "Login successful"})
    return jsonify({"error": "Invalid credentials"}), 401


@app.route("/upload_apk", methods=["POST"])
def upload_apk():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    file = request.files["file"]
    version = request.form.get("version")

    save_path = os.path.join("uploads", file.filename)
    os.makedirs("uploads", exist_ok=True)
    file.save(save_path)

    apk_data["version"] = version
    apk_data["filename"] = file.filename

    # Save metadata to GitHub
    github_push_file(APK_FILE, json.dumps(apk_data, indent=2))

    return jsonify({"message": "APK uploaded", "version": version})


@app.route("/download_apk", methods=["GET"])
def download_apk():
    if not apk_data.get("filename"):
        return jsonify({"error": "No APK available"}), 404
    path = os.path.join("uploads", apk_data["filename"])
    return send_file(path, as_attachment=True)


@app.route("/latest_version", methods=["GET"])
def latest_version():
    return jsonify(apk_data)


# ---------------- Main ---------------- #
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
