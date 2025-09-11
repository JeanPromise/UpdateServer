from flask import Flask, request, jsonify, send_from_directory
import os, json, base64, requests
from werkzeug.utils import secure_filename
from datetime import datetime

app = Flask(__name__, static_url_path='', static_folder='.')

# ---------------- Config ----------------
GITHUB_OWNER = "JeanPromise"
GITHUB_REPO = "UpdateServer"
BRANCH = "main"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

USERS_FILE = "users.json"
APK_FILE = "apk.json"
APK_FOLDER = "apks"

# ---------------- GitHub Helpers ----------------
def github_get_file(filename, default):
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{filename}?ref={BRANCH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        content = base64.b64decode(r.json()["content"]).decode()
        return json.loads(content)
    else:
        return default

def github_push_file(filename, content, message=None):
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{filename}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
    # get SHA if file exists
    r = requests.get(url, headers=headers)
    sha = r.json().get("sha") if r.status_code == 200 else None
    data = {
        "message": message or f"Update {filename} at {datetime.utcnow()}",
        "content": base64.b64encode(content.encode()).decode(),
        "branch": BRANCH
    }
    if sha:
        data["sha"] = sha
    res = requests.put(url, headers=headers, json=data)
    if res.status_code not in [200, 201]:
        print(f"❌ Failed to push {filename}: {res.text}")
    else:
        print(f"✅ {filename} pushed to GitHub")

# ---------------- Data Helpers ----------------
def load_users():
    return github_get_file(USERS_FILE, [])

def save_users(users_list):
    github_push_file(USERS_FILE, json.dumps(users_list, indent=2), "Update users")

def load_apk():
    return github_get_file(APK_FILE, {
        "version": "1.0.0",
        "changelog": "Initial release",
        "download_url": ""
    })

def save_apk(apk_obj):
    github_push_file(APK_FILE, json.dumps(apk_obj, indent=2), "Update APK data")

# ---------------- Index Pages ----------------
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/admin')
def admin():
    return send_from_directory('.', 'admin.html')

# ---------------- User Endpoints ----------------
@app.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    name = data.get('name')
    email = data.get('email')
    password = data.get('password')

    users = load_users()
    if any(u['email'] == email for u in users):
        return jsonify({"success": False, "message": "Email already registered."})

    users.append({"name": name, "email": email, "password": password, "enabled": True})
    save_users(users)
    return jsonify({"success": True})

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')

    users = load_users()
    for user in users:
        if user['email'] == email:
            if not user.get('enabled', True):
                return jsonify({"success": False, "message": "User is disabled."})
            if user['password'] == password:
                return jsonify({"success": True})
            return jsonify({"success": False, "message": "Incorrect password."})
    return jsonify({"success": False, "message": "Email not registered."})

@app.route('/get_users')
def get_users():
    return jsonify(load_users())

@app.route('/toggle_user', methods=['POST'])
def toggle_user():
    data = request.get_json()
    email = data.get('email')
    enable = data.get('enable', True)

    users = load_users()
    for user in users:
        if user['email'] == email:
            user['enabled'] = enable
            break
    save_users(users)
    return jsonify({"success": True})

@app.route('/enable_all', methods=['POST'])
def enable_all():
    users = load_users()
    for u in users:
        u['enabled'] = True
    save_users(users)
    return jsonify({"success": True})

@app.route('/disable_all', methods=['POST'])
def disable_all():
    users = load_users()
    for u in users:
        u['enabled'] = False
    save_users(users)
    return jsonify({"success": True})

# ---------------- APK Endpoints ----------------
@app.route('/check_update')
def check_update():
    apk_data = load_apk()
    return jsonify({
        "update_required": apk_data["version"] is not None,
        "apk_version": apk_data["version"],
        "url": apk_data.get("download_url")
    })

@app.route('/upload_apk', methods=['POST'])
def upload_apk():
    if 'apk' not in request.files or 'version' not in request.form:
        return jsonify({"success": False, "message": "APK file and version required."})

    file = request.files['apk']
    version = request.form['version']
    filename = secure_filename(file.filename)
    apk_bytes = file.read()

    # push APK binary to GitHub
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{APK_FOLDER}/{filename}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
    data = {"message": f"Upload APK {filename} version {version}",
            "content": base64.b64encode(apk_bytes).decode(),
            "branch": BRANCH}
    r = requests.put(url, headers=headers, json=data)
    if r.status_code not in [200, 201]:
        return jsonify({"success": False, "message": f"GitHub upload failed: {r.text}"}), 500

    download_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/main/{APK_FOLDER}/{filename}"
    apk_data = {
        "version": version,
        "changelog": f"Uploaded version {version}",
        "download_url": download_url
    }
    save_apk(apk_data)
    return jsonify({"success": True, "message": "APK uploaded.", "url": download_url})

@app.route('/download_apk')
def download_apk():
    apk_data = load_apk()
    if not apk_data.get("download_url"):
        return jsonify({"success": False, "message": "No APK available."}), 404
    return jsonify({"success": True, "url": apk_data["download_url"]})

@app.route('/get_apk')
def get_apk():
    return jsonify(load_apk())

@app.route('/update_apk', methods=['POST'])
def update_apk():
    data = request.get_json()
    new_version = data.get('version')
    new_changelog = data.get('changelog')
    new_download_url = data.get('download_url')

    apk_data = {
        "version": new_version,
        "changelog": new_changelog,
        "download_url": new_download_url
    }
    save_apk(apk_data)
    return jsonify({"success": True})

# ---------------- Run ----------------
if __name__ == '__main__':
    app.run(debug=True, port=5000)
