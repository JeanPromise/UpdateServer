import base64, json, requests, os
from flask import Flask, request, jsonify, send_from_directory, Response, session, redirect, url_for
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

app = Flask(__name__, static_url_path='', static_folder='.')
app.secret_key = os.getenv("FLASK_SECRET_KEY", "supersecretkey")  # required for session

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
    """
    Fetch a file from the repo's contents API and return decoded JSON content,
    or default if not present / parse fails.
    """
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{filename}?ref={BRANCH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
    r = requests.get(url, headers=headers)
    if r.status_code == 200:
        try:
            content = base64.b64decode(r.json().get("content", "")).decode()
            data = json.loads(content)
            return data if isinstance(data, (dict, list)) else default
        except Exception as e:
            print(f"⚠️ Failed to parse {filename}: {e}")
            return default
    return default

def github_push_file(filename, content, message=None):
    """
    Push (create/update) a file to the repo using the contents API.
    'content' is a string (will be JSON-encoded/encoded to bytes inside).
    """
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{filename}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}

    # Get SHA if file exists (so we can update)
    r_get = requests.get(url, headers=headers)
    sha = r_get.json().get("sha") if r_get.status_code == 200 else None

    data = {
        "message": message or f"Update {filename} at {datetime.utcnow().isoformat()}",
        "content": base64.b64encode(content.encode()).decode(),
        "branch": BRANCH
    }
    if sha:
        data["sha"] = sha

    r = requests.put(url, headers=headers, json=data)
    if r.status_code not in [200, 201]:
        print(f"❌ Failed to push {filename}: {r.status_code} {r.text}")
        return False
    return True

# ---------------- Data Helpers ----------------
def load_users():
    data = github_get_file(USERS_FILE, [])
    return data if isinstance(data, list) else []

def save_users(users_list):
    github_push_file(USERS_FILE, json.dumps(users_list, indent=2), "Update users")

# ensure default apk.json includes filename and sha keys
def load_apk():
    return github_get_file(APK_FILE, {"version": None, "changelog": "", "download_url": "", "filename": None, "sha": None})

def save_apk(apk_obj):
    # ensure keys exist
    apk_obj_out = {
        "version": apk_obj.get("version"),
        "changelog": apk_obj.get("changelog", ""),
        "download_url": apk_obj.get("download_url", ""),
        "filename": apk_obj.get("filename"),
        "sha": apk_obj.get("sha")
    }
    github_push_file(APK_FILE, json.dumps(apk_obj_out, indent=2), "Update APK data")

# ---------------- Private Site Enforcement ----------------
@app.before_request
def require_login():
    # allow public endpoints
    if request.endpoint in ['login', 'register', 'index', 'static', 'check_update', 'download_apk', 'get_apk']:
        return
    if 'user_email' not in session:
        return redirect(url_for('index'))

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
    hashed_pw = generate_password_hash(password)
    users.append({"name": name, "email": email, "password": hashed_pw, "enabled": True, "login_history": []})
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
            if check_password_hash(u.get('password'), password):
                session['user_email'] = email

                ip = request.headers.get('X-Forwarded-For', request.remote_addr)
                user_agent = request.headers.get("User-Agent", "")
                try:
                    loc_res = requests.get(f"http://ip-api.com/json/{ip}").json()
                    country = loc_res.get("country", "Unknown")
                except:
                    country = "Unknown"
                login_record = {
                    "timestamp": datetime.utcnow().isoformat(),
                    "ip": ip,
                    "country": country,
                    "user_agent": user_agent
                }
                u.setdefault("login_history", []).append(login_record)
                save_users(users)
                return jsonify({"success": True})
            return jsonify({"success": False, "message": "Incorrect password."})
    return jsonify({"success": False, "message": "Email not registered."})

@app.route('/logout')
def logout():
    session.pop('user_email', None)
    return redirect(url_for('index'))

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

# ---------------- Login Analytics ----------------
@app.route('/login_analytics')
def login_analytics():
    users = load_users()
    analytics = []
    for u in users:
        if isinstance(u, dict):
            analytics.append({
                "name": u.get("name"),
                "email": u.get("email"),
                "enabled": u.get("enabled"),
                "total_logins": len(u.get("login_history", [])),
                "last_login": u.get("login_history", [])[-1] if u.get("login_history") else None
            })
    return jsonify(analytics)

# ---------------- APK Endpoints ----------------

# Download and stream the current APK (serves filename saved in metadata)
@app.route('/download_apk')
def download_apk():
    apk_data = load_apk()
    if not apk_data.get("download_url"):
        return jsonify({"success": False, "message": "No APK available."}), 404

    r = requests.get(apk_data["download_url"], stream=True)
    if r.status_code != 200:
        return jsonify({"success": False, "message": "Failed to fetch APK"}), 500

    # Use saved filename if available, fallback to app-latest.apk
    filename = apk_data.get("filename") or "app-latest.apk"

    return Response(
        r.iter_content(chunk_size=8192),
        content_type="application/vnd.android.package-archive",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

# Upload: versioned filename, save metadata + sha
@app.route('/upload_apk', methods=['POST'])
def upload_apk():
    if 'apk' not in request.files or 'version' not in request.form:
        return jsonify({"success": False, "message": "APK file and version required."}), 400

    file = request.files['apk']
    version = request.form['version'].strip()
    if not version:
        return jsonify({"success": False, "message": "Version cannot be empty."}), 400

    # Always save with versioned filename to avoid raw CDN cache problems
    filename = secure_filename(f"app-v{version}.apk")
    apk_bytes = file.read()

    api_path = f"{APK_FOLDER}/{filename}"
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{api_path}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}

    data = {
        "message": f"Upload APK {filename} version {version}",
        "content": base64.b64encode(apk_bytes).decode(),
        "branch": BRANCH
    }

    r = requests.put(url, headers=headers, json=data)
    if r.status_code not in [200, 201]:
        return jsonify({"success": False, "message": f"GitHub upload failed: {r.status_code} {r.text}"}), 500

    # Extract SHA if returned (useful for force-deletes)
    try:
        resp_json = r.json()
        file_sha = resp_json.get("content", {}).get("sha")
    except Exception:
        file_sha = None

    download_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{BRANCH}/{APK_FOLDER}/{filename}"

    # Save metadata including filename + sha
    save_apk({
        "version": version,
        "changelog": f"Uploaded version {version}",
        "download_url": download_url,
        "filename": filename,
        "sha": file_sha
    })

    return jsonify({"success": True, "message": "APK uploaded.", "url": download_url})

# Metadata-only delete (clears apk.json so clients no longer see update)
@app.route('/delete_apk', methods=['POST'])
def delete_apk():
    apk_data = load_apk()
    if not apk_data.get("download_url"):
        return jsonify({"success": False, "message": "No APK to delete."}), 400

    # Clear metadata only; file remains in repo
    save_apk({"version": None, "changelog": "", "download_url": "", "filename": None, "sha": None})
    return jsonify({"success": True, "message": "APK metadata deleted."})

# Force delete: remove actual APK file from GitHub (requires GITHUB_TOKEN with repo permissions)
@app.route('/delete_apk_force', methods=['POST'])
def delete_apk_force():
    apk_data = load_apk()
    filename = apk_data.get("filename")
    sha = apk_data.get("sha")

    if not filename or not sha:
        return jsonify({"success": False, "message": "No filename/sha saved - cannot force delete."}), 400

    api_path = f"{APK_FOLDER}/{filename}"
    url = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{api_path}"
    if not GITHUB_TOKEN:
        return jsonify({"success": False, "message": "GITHUB_TOKEN is required to force delete."}), 400

    headers = {"Authorization": f"token {GITHUB_TOKEN}"}
    payload = {"message": f"Delete APK {filename}", "sha": sha, "branch": BRANCH}

    r = requests.delete(url, headers=headers, json=payload)
    if r.status_code not in [200, 204]:
        return jsonify({"success": False, "message": f"GitHub delete failed: {r.status_code} {r.text}"}), 500

    # Clear metadata after successful delete
    save_apk({"version": None, "changelog": "", "download_url": "", "filename": None, "sha": None})
    return jsonify({"success": True, "message": f"APK {filename} deleted from GitHub."})

# Check update endpoint
@app.route('/check_update')
def check_update():
    apk_data = load_apk()
    has_apk = bool(apk_data.get("download_url"))
    return jsonify({
        "update_required": has_apk,
        "apk_version": apk_data.get("version") if has_apk else None,
        "url": apk_data.get("download_url") if has_apk else None
    })

@app.route('/get_apk')
def get_apk():
    return jsonify(load_apk())

@app.route('/update_apk', methods=['POST'])
def update_apk():
    data = request.get_json()
    save_apk({
        "version": data.get('version'),
        "changelog": data.get('changelog'),
        "download_url": data.get('download_url'),
        "filename": data.get('filename'),
        "sha": data.get('sha')
    })
    return jsonify({"success": True})

# ---------------- Run ----------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))  # Render requires binding to $PORT
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False
    )
