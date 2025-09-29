# app.py (replacement)
import base64
import json
import requests
import os
import logging
from flask import (
    Flask, request, jsonify, send_from_directory, Response, session, redirect, url_for
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime

# --- Basic logging ---
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("UpdateServer")

app = Flask(__name__, static_url_path='', static_folder='.')
app.secret_key = os.getenv("FLASK_SECRET_KEY", "supersecretkey")

# ---------------- Config ----------------
GITHUB_OWNER = "JeanPromise"
GITHUB_REPO = "UpdateServer"
BRANCH = "main"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

USERS_FILE = "users.json"
APK_FILE = "apk.json"
APK_FOLDER = "apks"

GITHUB_API_BASE = "https://api.github.com"

# --- Helper to build headers for GitHub API calls ---
def gh_headers():
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "UpdateServer-App"
    }
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    return headers

# ---------------- GitHub Helpers ----------------
def github_get_file(filename, default):
    """
    Fetch a file's JSON content from the repo. Returns 'default' if not found
    or parse fails. Logs helpful messages for debugging.
    """
    url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{filename}?ref={BRANCH}"
    try:
        r = requests.get(url, headers=gh_headers(), timeout=20)
    except Exception as e:
        log.exception("GitHub GET exception for %s", filename)
        return default

    if r.status_code == 200:
        body = r.json()
        content = body.get("content", "")
        encoding = body.get("encoding", "base64")
        try:
            if encoding == "base64":
                raw = base64.b64decode(content).decode()
            else:
                raw = content
            data = json.loads(raw)
            return data
        except Exception as e:
            log.exception("Failed to decode/parse %s from GitHub: %s", filename, e)
            return default

    # 404 => file not present; 403/401 => auth or rate-limit issue
    log.warning("GitHub GET %s returned %s: %s", filename, r.status_code, r.text[:200])
    return default

def github_get_file_metadata(filename):
    """Return GitHub content API metadata object (json) or None."""
    url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{filename}?ref={BRANCH}"
    try:
        r = requests.get(url, headers=gh_headers(), timeout=20)
        if r.status_code == 200:
            return r.json()
        log.warning("metadata GET %s returned %s", filename, r.status_code)
    except Exception as e:
        log.exception("metadata GET exception %s", filename)
    return None

def github_push_file(filename, content_str, message=None):
    """
    Push create/update to repo. Returns tuple (success:bool, response_json_or_text).
    Requires GITHUB_TOKEN for authenticated push.
    """
    if not GITHUB_TOKEN:
        err = "GITHUB_TOKEN missing — cannot push to repo."
        log.error(err)
        return False, err

    url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{filename}"
    headers = gh_headers()

    # get existing sha (if any)
    r_get = requests.get(url, headers=headers, timeout=20)
    sha = None
    if r_get.status_code == 200:
        try:
            sha = r_get.json().get("sha")
        except Exception:
            sha = None

    payload = {
        "message": message or f"Update {filename} at {datetime.utcnow().isoformat()}",
        "content": base64.b64encode(content_str.encode()).decode(),
        "branch": BRANCH
    }
    if sha:
        payload["sha"] = sha

    try:
        r = requests.put(url, headers=headers, json=payload, timeout=60)
    except Exception as e:
        log.exception("GitHub PUT exception for %s", filename)
        return False, str(e)

    if r.status_code in (200, 201):
        return True, r.json()
    else:
        log.error("GitHub PUT failed %s: %s", r.status_code, r.text[:500])
        return False, r.text

# ---------------- Data Helpers ----------------
def load_users():
    data = github_get_file(USERS_FILE, [])
    if not isinstance(data, list):
        log.warning("%s did not return a list; returning empty list", USERS_FILE)
        return []
    return data

def save_users(users_list):
    ok, resp = github_push_file(USERS_FILE, json.dumps(users_list, indent=2), "Update users")
    if not ok:
        log.error("Failed saving users.json: %s", resp)
        return False, resp
    return True, resp

def load_apk():
    default = {"version": None, "changelog": "", "download_url": "", "filename": None, "sha": None}
    data = github_get_file(APK_FILE, default)
    if not isinstance(data, dict):
        return default
    # ensure keys exist
    for k in default:
        data.setdefault(k, default[k])
    return data

def save_apk(apk_obj):
    apk_out = {
        "version": apk_obj.get("version"),
        "changelog": apk_obj.get("changelog", ""),
        "download_url": apk_obj.get("download_url", ""),
        "filename": apk_obj.get("filename"),
        "sha": apk_obj.get("sha")
    }
    ok, resp = github_push_file(APK_FILE, json.dumps(apk_out, indent=2), "Update APK data")
    if not ok:
        log.error("Failed saving apk.json: %s", resp)
        return False, resp
    return True, resp

# ---------------- Private Site Enforcement ----------------
@app.before_request
def require_login():
    # allow public endpoints
    public = {'login', 'register', 'index', 'static', 'check_update', 'download_apk', 'get_apk'}
    ep = request.endpoint
    if ep in public:
        return
    if 'user_email' not in session:
        # return a JSON error for API calls and redirect for page loads
        if request.path.startswith('/api') or request.is_json or request.path.startswith('/get_') or request.path.startswith('/login_analytics'):
            return jsonify({"success": False, "message": "Authentication required."}), 401
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
    data = request.get_json() or {}
    name, email, password = data.get('name'), data.get('email'), data.get('password')
    if not (name and email and password):
        return jsonify({"success": False, "message": "name, email, password required."}), 400
    users = load_users()
    if any(isinstance(u, dict) and u.get('email') == email for u in users):
        return jsonify({"success": False, "message": "Email already registered."})
    hashed_pw = generate_password_hash(password)
    users.append({"name": name, "email": email, "password": hashed_pw, "enabled": True, "login_history": []})
    ok, resp = save_users(users)
    if not ok:
        return jsonify({"success": False, "message": f"Failed to save users: {resp}"}), 500
    return jsonify({"success": True})

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json() or {}
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
                    loc_res = requests.get(f"http://ip-api.com/json/{ip}", timeout=5).json()
                    country = loc_res.get("country", "Unknown")
                except Exception:
                    country = "Unknown"
                login_record = {
                    "timestamp": datetime.utcnow().isoformat(),
                    "ip": ip,
                    "country": country,
                    "user_agent": user_agent
                }
                u.setdefault("login_history", []).append(login_record)
                ok, resp = save_users(users)
                if not ok:
                    # still allow login but warn
                    log.error("Failed to save login history: %s", resp)
                return jsonify({"success": True})
            return jsonify({"success": False, "message": "Incorrect password."})
    return jsonify({"success": False, "message": "Email not registered."})

@app.route('/logout')
def logout():
    session.pop('user_email', None)
    return redirect(url_for('index'))

@app.route('/get_users')
def get_users():
    users = load_users()
    return jsonify(users)

@app.route('/toggle_user', methods=['POST'])
def toggle_user():
    data = request.get_json() or {}
    email, enable = data.get('email'), data.get('enable', True)
    users = load_users()
    changed = False
    for u in users:
        if isinstance(u, dict) and u.get('email') == email:
            u['enabled'] = bool(enable)
            changed = True
            break
    if not changed:
        return jsonify({"success": False, "message": "User not found."}), 404
    ok, resp = save_users(users)
    if not ok:
        return jsonify({"success": False, "message": f"Failed saving users: {resp}"}), 500
    return jsonify({"success": True})

@app.route('/enable_all', methods=['POST'])
def enable_all():
    users = load_users()
    for u in users:
        if isinstance(u, dict):
            u['enabled'] = True
    ok, resp = save_users(users)
    if not ok:
        return jsonify({"success": False, "message": resp}), 500
    return jsonify({"success": True})

@app.route('/disable_all', methods=['POST'])
def disable_all():
    users = load_users()
    for u in users:
        if isinstance(u, dict):
            u['enabled'] = False
    ok, resp = save_users(users)
    if not ok:
        return jsonify({"success": False, "message": resp}), 500
    return jsonify({"success": True})

# ---------------- Login Analytics ----------------
@app.route('/login_analytics')
def login_analytics():
    users = load_users()
    analytics = []
    for u in users:
        if isinstance(u, dict):
            last = (u.get("login_history") or [])[-1] if (u.get("login_history") or []) else None
            analytics.append({
                "name": u.get("name"),
                "email": u.get("email"),
                "enabled": u.get("enabled"),
                "total_logins": len(u.get("login_history", [])),
                "last_login": last
            })
    return jsonify(analytics)

# ---------------- APK Endpoints ----------------

@app.route('/download_apk')
def download_apk():
    apk_data = load_apk()
    if not apk_data.get("download_url"):
        return jsonify({"success": False, "message": "No APK available."}), 404
    r = requests.get(apk_data["download_url"], stream=True, timeout=30)
    if r.status_code != 200:
        log.error("Failed to fetch raw APK: %s", r.status_code)
        return jsonify({"success": False, "message": "Failed to fetch APK"}), 500
    filename = apk_data.get("filename") or "app-latest.apk"
    return Response(
        r.iter_content(chunk_size=8192),
        content_type="application/vnd.android.package-archive",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@app.route('/upload_apk', methods=['POST'])
def upload_apk():
    if 'apk' not in request.files or 'version' not in request.form:
        return jsonify({"success": False, "message": "APK file and version required."}), 400
    file = request.files['apk']
    version = request.form['version'].strip()
    if not version:
        return jsonify({"success": False, "message": "Version cannot be empty."}), 400

    filename = secure_filename(f"app-v{version}.apk")
    apk_bytes = file.read()
    api_path = f"{APK_FOLDER}/{filename}"
    url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{api_path}"
    headers = gh_headers()

    if not GITHUB_TOKEN:
        return jsonify({"success": False, "message": "Server missing GITHUB_TOKEN (cannot upload)."}), 500

    data = {
        "message": f"Upload APK {filename} version {version}",
        "content": base64.b64encode(apk_bytes).decode(),
        "branch": BRANCH
    }

    r = requests.put(url, headers=headers, json=data, timeout=120)
    if r.status_code not in [200, 201]:
        log.error("GitHub upload failed: %s %s", r.status_code, r.text[:500])
        return jsonify({"success": False, "message": f"GitHub upload failed: {r.status_code}"}), 500

    # Get sha from response or from metadata GET if missing
    file_sha = None
    try:
        resp_json = r.json()
        file_sha = resp_json.get("content", {}).get("sha")
    except Exception:
        file_sha = None

    if not file_sha:
        # try to fetch metadata explicitly
        meta = github_get_file_metadata(api_path)
        if meta:
            file_sha = meta.get("sha")

    download_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{BRANCH}/{APK_FOLDER}/{filename}"

    ok, resp = save_apk({
        "version": version,
        "changelog": f"Uploaded version {version}",
        "download_url": download_url,
        "filename": filename,
        "sha": file_sha
    })
    if not ok:
        return jsonify({"success": False, "message": f"Uploaded APK but failed to save apk.json: {resp}"}), 500

    return jsonify({"success": True, "message": "APK uploaded.", "url": download_url, "sha": file_sha})

@app.route('/delete_apk', methods=['POST'])
def delete_apk():
    apk_data = load_apk()
    if not apk_data.get("download_url"):
        return jsonify({"success": False, "message": "No APK to delete."}), 400
    ok, resp = save_apk({"version": None, "changelog": "", "download_url": "", "filename": None, "sha": None})
    if not ok:
        return jsonify({"success": False, "message": f"Failed clearing apk.json: {resp}"}), 500
    return jsonify({"success": True, "message": "APK metadata deleted."})

@app.route('/delete_apk_force', methods=['POST'])
def delete_apk_force():
    apk_data = load_apk()
    filename = apk_data.get("filename")
    sha = apk_data.get("sha")
    if not filename:
        return jsonify({"success": False, "message": "No filename saved - cannot force delete."}), 400

    api_path = f"{APK_FOLDER}/{filename}"
    # Try to fetch SHA if not present
    if not sha:
        meta = github_get_file_metadata(api_path)
        if meta:
            sha = meta.get("sha")

    if not sha:
        return jsonify({"success": False, "message": "No SHA available for the file; cannot delete."}), 400

    if not GITHUB_TOKEN:
        return jsonify({"success": False, "message": "GITHUB_TOKEN is required to force delete."}), 400

    url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{api_path}"
    headers = gh_headers()
    payload = {"message": f"Delete APK {filename}", "sha": sha, "branch": BRANCH}

    r = requests.delete(url, headers=headers, json=payload, timeout=30)
    if r.status_code not in [200, 204]:
        log.error("GitHub delete failed: %s %s", r.status_code, r.text[:500])
        return jsonify({"success": False, "message": f"GitHub delete failed: {r.status_code}"}), 500

    # clear apk.json metadata
    ok, resp = save_apk({"version": None, "changelog": "", "download_url": "", "filename": None, "sha": None})
    if not ok:
        return jsonify({"success": False, "message": f"Deleted file but failed to clear apk.json: {resp}"}), 500

    return jsonify({"success": True, "message": f"APK {filename} deleted from GitHub."})

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
    data = request.get_json() or {}
    ok, resp = save_apk({
        "version": data.get('version'),
        "changelog": data.get('changelog'),
        "download_url": data.get('download_url'),
        "filename": data.get('filename'),
        "sha": data.get('sha')
    })
    if not ok:
        return jsonify({"success": False, "message": resp}), 500
    return jsonify({"success": True})

# ---------------- Run ----------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False
    )
