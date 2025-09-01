import os
import json
from flask import Flask, request, send_from_directory, render_template, jsonify
from datetime import datetime

app = Flask(__name__)
APK_FOLDER = "apks"
USERS_FILE = "users.json"
VERSIONS_FILE = "apk_versions.json"

os.makedirs(APK_FOLDER, exist_ok=True)

# -------------------------------
# Helpers
# -------------------------------

def load_users():
    if not os.path.exists(USERS_FILE):
        return []
    with open(USERS_FILE, "r") as f:
        return json.load(f)

def save_users(users):
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)

def load_versions():
    if not os.path.exists(VERSIONS_FILE):
        return []
    with open(VERSIONS_FILE, "r") as f:
        return json.load(f)

def save_versions(versions):
    with open(VERSIONS_FILE, "w") as f:
        json.dump(versions, f, indent=2)

def next_version():
    versions = load_versions()
    if not versions:
        return "1.0.0"
    last = versions[-1]["version"]
    major, minor, patch = map(int, last.split("."))
    patch += 1
    return f"{major}.{minor}.{patch}"

# -------------------------------
# Routes
# -------------------------------

@app.route("/")
def index():
    users = load_users()
    versions = load_versions()
    latest_apk = versions[-1]["filename"] if versions else None
    return render_template("index.html", users=users, latest_apk=latest_apk, versions=versions)

@app.route("/upload", methods=["POST"])
def upload_apk():
    if "apkfile" not in request.files:
        return "No file uploaded", 400
    file = request.files["apkfile"]
    version = next_version()
    filename = f"{version}_{file.filename}"
    filepath = os.path.join(APK_FOLDER, filename)
    file.save(filepath)

    # Save version info
    versions = load_versions()
    versions.append({"version": version, "filename": filename, "uploaded_at": datetime.now().isoformat()})
    save_versions(versions)

    return f"Uploaded {filename} successfully!"

@app.route("/apks/<filename>")
def serve_apk(filename):
    return send_from_directory(APK_FOLDER, filename)

@app.route("/register_device", methods=["POST"])
def register_device():
    """
    Endpoint for APKs to ping the server (simulated online status)
    Expect JSON: { "device_id": "...", "username": "..." }
    """
    data = request.get_json()
    if not data or "device_id" not in data or "username" not in data:
        return "Invalid data", 400

    users = load_users()
    # Update existing or add new
    for u in users:
        if u["device_id"] == data["device_id"]:
            u["last_seen"] = datetime.now().isoformat()
            u["online"] = True
            save_users(users)
            return "Updated"
    # New user
    users.append({
        "device_id": data["device_id"],
        "username": data["username"],
        "registered_at": datetime.now().isoformat(),
        "last_seen": datetime.now().isoformat(),
        "online": True
    })
    save_users(users)
    return "Registered"

@app.route("/users")
def get_users():
    """
    Return JSON list of users with online status
    """
    users = load_users()
    # Mark offline if last_seen > 5 minutes ago (optional)
    now = datetime.now()
    for u in users:
        last_seen = datetime.fromisoformat(u["last_seen"])
        if (now - last_seen).total_seconds() > 300:
            u["online"] = False
    save_users(users)
    return jsonify(users)

# -------------------------------
# Run
# -------------------------------

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
