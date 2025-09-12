import base64, json, requests, os
from flask import Flask, request, jsonify, send_from_directory
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
        try:
            content = base64.b64decode(r.json()["content"]).decode()
            data = json.loads(content)
            return data if isinstance(data, (dict, list)) else default
        except Exception as e:
            print(f"⚠️ Failed to parse {filename}: {e}")
            return default
    return default

def github_push_file(filename, content, message=None):
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{filename}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}

    r_get = requests.get(url, headers=headers)
    sha = r_get.json().get("sha") if r_get.status_code == 200 else None

    data = {
        "message": message or f"Update {filename} at {datetime.utcnow()}",
        "content": base64.b64encode(content.encode()).decode(),
        "branch": BRANCH
    }
    if sha:
        data["sha"] = sha

    r = requests.put(url, headers=headers, json=data)
    if r.status_code not in [200, 201]:
        print(f"❌ Failed to push {filename}: {r.text}")
        return False
    return True

# ---------------- Data Helpers ----------------
def load_users():
    users = github_get_file(USERS_FILE, [])
    if not isinstance(users, list):
        return []
    return [u for u in users if isinstance(u, dict) and "email" in u and "password" in u]

def save_users(users_list):
    github_push_file(USERS_FILE, json.dumps(users_list, indent=2), "Update users")

def load_apk():
    apk = github_get_file(APK_FILE, {"version": None, "changelog": "", "download_url": ""})
    return apk if isinstance(apk, dict) else {"version": None, "changelog": "", "download_url": ""}

def save_apk(apk_obj):
    github_push_file(APK_FILE, json.dumps(apk_obj, indent=2), "Update APK data")

# ---------------- Pages ----------------
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
    name, email, password = data.get('name'), data.get('email'), data.get('password')
    users = load_users()
    if any(u.get('email').lower() == email.lower() for u in users):
        return jsonify({"success": False, "message": "Email already registered."})
    users.append({"name": name, "email": email, "password": password, "enabled": True})
    save_users(users)
    return jsonify({"success": True})

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    email, password = data.get('email'), data.get('password')
    users = load_users()
    for u in users:
        if u.get('email', '').lower() == email.lower():
            if not u.get('enabled', True):
                return jsonify({"success": False, "message": "User is disabled."})
            if u.get('password') == password:
                return jsonify({"success": True})
            return jsonify({"success": False, "message": "Incorrect password."})
    return jsonify({"success": False, "message": "Email not registered."})

# ---------------- APK Endpoints ----------------
@app.route('/check_update')
def check_update():
    apk_data = load_apk()
    has_apk = bool(apk_data.get("download_url"))
    return jsonify({
        "update_required": has_apk,
        "apk_version": apk_data.get("version") if has_apk else None,
        "url": apk_data.get("download_url") if has_apk else None
    })

@app.route('/download_apk')
def download_apk():
    apk_data = load_apk()
    if not apk_data.get("download_url"):
        return jsonify({"success": False, "message": "No APK available."}), 404
    return jsonify({"success": True, "url": apk_data["download_url"]})

# ---------------- Run ----------------
if __name__ == '__main__':
    app.run(debug=True, port=5000)
