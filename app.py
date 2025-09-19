import base64, json, requests, os 
from flask import Flask, request, jsonify, send_from_directory, Response
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

    # Get SHA if file exists
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
    data = github_get_file(USERS_FILE, [])
    return data if isinstance(data, list) else []

def save_users(users_list):
    github_push_file(USERS_FILE, json.dumps(users_list, indent=2), "Update users")

def load_apk():
    return github_get_file(APK_FILE, {"version": None, "changelog": "", "download_url": ""})

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
    if any(u.get('email') == email for u in users if isinstance(u, dict)):
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
        if isinstance(u, dict) and u.get('email') == email:
            if not u.get('enabled', True):
                return jsonify({"success": False, "message": "User is disabled."})
            if u.get('password') == password:
                return jsonify({"success": True})
            return jsonify({"success": False, "message": "Incorrect password."})
    return jsonify({"success": False, "message": "Email not registered."})

@app.route('/get_users')
def get_users():
    return jsonify(load_users())

@app.route('/toggle_user', methods=['POST'])
def toggle_user():
    data = request.get_json()
    email, enable = data.get('email'), data.get('enable', True)
    users = load_users()
    for u in users:
        if isinstance(u, dict) and u.get('email') == email:
            u['enabled'] = enable
            break
    save_users(users)
    return jsonify({"success": True})

@app.route('/enable_all', methods=['POST'])
def enable_all():
    users = load_users()
    for u in users:
        if isinstance(u, dict):
            u['enabled'] = True
    save_users(users)
    return jsonify({"success": True})

@app.route('/disable_all', methods=['POST'])
def disable_all():
    users = load_users()
    for u in users:
        if isinstance(u, dict):
            u['enabled'] = False
    save_users(users)
    return jsonify({"success": True})

# ---------------- APK Endpoints ----------------
@app.route('/check_update', methods=['POST'])
def check_update():
    data = request.get_json(silent=True) or {}
    installed_version = data.get("installed_version")  # what the app reports
    apk_data = load_apk()
    latest_version = apk_data.get("version")
    download_url = apk_data.get("download_url")

    # No APK uploaded at all
    if not latest_version or not download_url:
        return jsonify({"update_required": False})

    # If installed version equals latest → no update required
    if installed_version == latest_version:
        return jsonify({
            "update_required": False,
            "apk_version": latest_version
        })

    # Otherwise update is required
    return jsonify({
        "update_required": True,
        "apk_version": latest_version,
        "url": download_url
    })


@app.route('/upload_apk', methods=['POST'])
def upload_apk():
    if 'apk' not in request.files or 'version' not in request.form:
        return jsonify({"success": False, "message": "APK file and version required."})

    file = request.files['apk']
    version = request.form['version']
    filename = secure_filename(file.filename)
    apk_bytes = file.read()

    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{APK_FOLDER}/{filename}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}

    # Get SHA if file exists
    r_get = requests.get(url, headers=headers)
    sha = r_get.json().get("sha") if r_get.status_code == 200 else None

    data = {
        "message": f"Upload APK {filename} version {version}",
        "content": base64.b64encode(apk_bytes).decode(),
        "branch": BRANCH
    }
    if sha:
        data["sha"] = sha

    r = requests.put(url, headers=headers, json=data)
    if r.status_code not in [200, 201]:
        return jsonify({"success": False, "message": f"GitHub upload failed: {r.text}"}), 500

    download_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/main/{APK_FOLDER}/{filename}?v={version}"
    save_apk({"version": version, "changelog": f"Uploaded version {version}", "download_url": download_url})
    return jsonify({"success": True, "message": "APK uploaded.", "url": download_url})

@app.route('/download_apk')
def download_apk():
    apk_data = load_apk()
    if not apk_data.get("download_url"):
        return jsonify({"success": False, "message": "No APK available."}), 404

    # Fetch APK from GitHub and stream with correct headers
    r = requests.get(apk_data["download_url"], stream=True)
    if r.status_code != 200:
        return jsonify({"success": False, "message": "Failed to fetch APK"}), 500

    return Response(
        r.iter_content(chunk_size=8192),
        content_type="application/vnd.android.package-archive",
        headers={"Content-Disposition": "attachment; filename=app-latest.apk"}
    )

@app.route('/get_apk')
def get_apk():
    return jsonify(load_apk())

@app.route('/update_apk', methods=['POST'])
def update_apk():
    data = request.get_json()
    save_apk({
        "version": data.get('version'),
        "changelog": data.get('changelog'),
        "download_url": data.get('download_url')
    })
    return jsonify({"success": True})

# ---------------- Delete APK ----------------
@app.route('/delete_apk', methods=['POST'])
def delete_apk():
    apk_data = load_apk()
    if not apk_data.get("download_url"):
        return jsonify({"success": False, "message": "No APK to delete."})

    # Reset apk.json
    save_apk({"version": None, "changelog": "", "download_url": ""})
    return jsonify({"success": True, "message": "APK deleted."})

# ---------------- Run ----------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))  # Render requires binding to $PORT
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False
    )
