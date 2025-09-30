# app.py (final — preserves your endpoints + guarded admin access)
import base64
import json
import requests
import os
import logging
import hashlib
from datetime import datetime

from flask import (
    Flask, request, jsonify, send_from_directory, Response,
    session, redirect, url_for, abort
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash

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

# ---------------- Utilities ----------------
def hash_email(email: str) -> str:
    """Stable one-way hash for the admin email; store the hash instead of raw email."""
    return hashlib.sha256(email.encode()).hexdigest()

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
    url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{filename}?ref={BRANCH}"
    try:
        r = requests.get(url, headers=gh_headers(), timeout=20)
    except Exception:
        log.exception("GitHub GET exception for %s", filename)
        return default

    if r.status_code == 200:
        try:
            body = r.json()
            content = body.get("content", "")
            encoding = body.get("encoding", "base64")
            if encoding == "base64":
                raw = base64.b64decode(content).decode()
            else:
                raw = content
            return json.loads(raw)
        except Exception:
            log.exception("Failed to decode/parse %s", filename)
            return default

    log.warning("GitHub GET %s returned %s", filename, r.status_code)
    return default

def github_get_file_metadata(filename):
    url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{filename}?ref={BRANCH}"
    try:
        r = requests.get(url, headers=gh_headers(), timeout=20)
        if r.status_code == 200:
            return r.json()
    except Exception:
        log.exception("metadata GET exception %s", filename)
    return None

def github_push_file(filename, content_str, message=None):
    if not GITHUB_TOKEN:
        err = "GITHUB_TOKEN missing — cannot push to repo."
        log.error(err)
        return False, err

    url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{filename}"
    headers = gh_headers()

    try:
        r_get = requests.get(url, headers=headers, timeout=20)
    except Exception:
        r_get = None

    sha = None
    if r_get and r_get.status_code == 200:
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
        log.error("GitHub PUT failed %s: %s", r.status_code, (r.text or "")[:500])
        return False, r.text

# ---------------- Data Helpers ----------------
def load_users():
    data = github_get_file(USERS_FILE, [])
    return data if isinstance(data, list) else []

def save_users(users_list):
    return github_push_file(USERS_FILE, json.dumps(users_list, indent=2), "Update users")

def load_apk():
    default = {"version": None, "changelog": "", "download_url": "", "filename": None, "sha": None}
    data = github_get_file(APK_FILE, default)
    if not isinstance(data, dict):
        return default
    for k in default:
        data.setdefault(k, default[k])
    return data

def save_apk(apk_obj):
    return github_push_file(APK_FILE, json.dumps(apk_obj, indent=2), "Update APK data")

# ---------------- Public/Private Enforcement ----------------
@app.before_request
def require_login():
    # endpoints that remain public
    public = {
        'login', 'register', 'index', 'admin',
        'get_users', 'check_update', 'download_apk',
        'get_apk', 'simplemindserverisgone',
        'simplemind_login', 'logout'
    }
    ep = request.endpoint
    if ep in public:
        return
    if 'user_email' not in session:
        # for API-ish paths return JSON error
        if request.path.startswith('/api') or request.is_json or request.path.startswith('/get_') or request.path.startswith('/login_analytics'):
            return jsonify({"success": False, "message": "Authentication required."}), 401
        return redirect(url_for('index'))

# ---------------- Pages ----------------
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

# ---------------- Admin Restriction ----------------
# Direct /admin.html is blocked below; /admin is only allowed after simple gate
@app.route('/admin')
def admin_dashboard():
    # require session flag set by simplemind_login
    if not session.get('simple_admin'):
        return redirect('/simplemindserverisgone')
    return send_from_directory('.', 'admin.html')

@app.route('/admin.html')
def block_direct_admin():
    return "Not today buddy", 403

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
        if not isinstance(u, dict):
            continue
        # regular users store 'email' while admin gate stores 'email_hash'
        if u.get('email') == email:
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
                    log.error("Failed to save login history: %s", resp)
                return jsonify({"success": True})
            return jsonify({"success": False, "message": "Incorrect password."})
    return jsonify({"success": False, "message": "Email not registered."})

@app.route('/logout')
def logout():
    session.pop('user_email', None)
    session.pop('simple_admin', None)
    return redirect(url_for('index'))

@app.route('/get_users')
def get_users():
    users = load_users()
    out = []
    for u in users:
        if isinstance(u, dict):
            # hide password and hashed email field before returning
            u_copy = {k: v for k, v in u.items() if k not in ('password', 'email_hash')}
            out.append(u_copy)
    return jsonify(out)

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
        if not isinstance(u, dict):
            continue
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
        log.error("GitHub upload failed: %s %s", r.status_code, (r.text or "")[:500])
        return jsonify({"success": False, "message": f"GitHub upload failed: {r.status_code}"}), 500

    # Get sha from response or metadata
    file_sha = None
    try:
        file_sha = r.json().get("content", {}).get("sha")
    except Exception:
        file_sha = None
    if not file_sha:
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
    ok, resp = save_apk({"version": None, "changelog": "", "download_url": "", "filename": None, "sha": None})
    if not ok:
        return jsonify({"success": False, "message": resp}), 500
    return jsonify({"success": True, "message": "APK metadata deleted."})

@app.route('/delete_apk_force', methods=['POST'])
def delete_apk_force():
    apk_data = load_apk()
    filename = apk_data.get("filename")
    sha = apk_data.get("sha")
    if not filename:
        return jsonify({"success": False, "message": "No filename saved - cannot force delete."}), 400

    api_path = f"{APK_FOLDER}/{filename}"
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
        log.error("GitHub delete failed: %s %s", r.status_code, (r.text or "")[:500])
        return jsonify({"success": False, "message": f"GitHub delete failed: {r.status_code}"}), 500

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

# ---------------- Simple Admin Gate ----------------
@app.route('/simplemindserverisgone')
def simplemindserverisgone():
    # Tiny login page (email + password)
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8"/>
        <title>Secure Admin Login</title>
        <style>
          body{font-family:Arial, sans-serif;padding:20px;background:#111;color:#fff}
          .box{max-width:360px;margin:40px auto;padding:20px;background:#1a1a1a;border-radius:8px}
          input{width:100%;padding:8px;margin:6px 0;border-radius:4px;border:1px solid #333;background:#000;color:#fff}
          button{padding:10px 14px;border-radius:6px;border:none;cursor:pointer;background:#4CAF50;color:#fff}
        </style>
    </head>
    <body>
      <div class="box">
        <h3>Admin Login</h3>
        <form method="POST" action="/simplemind_login">
          <label>Email</label><br/>
          <input type="email" name="email" required autofocus /><br/>
          <label>Password</label><br/>
          <input type="password" name="password" required /><br/><br/>
          <button type="submit">Login</button>
        </form>
        <p style="color:#bbb;font-size:12px;margin-top:10px">
          First time: your provided email+password become the admin (stored as hashed values in users.json).
        </p>
      </div>
    </body>
    </html>
    """

@app.route('/simplemind_login', methods=['POST'])
def simplemind_login():
    email = request.form.get("email")
    password = request.form.get("password")

    if not email or not password:
        return "Email and password required", 400

    email_clean = email.strip().lower()
    email_hash = hash_email(email_clean)

    users = load_users()
    # look for stored admin by email_hash (we do not store plain admin email)
    admin_user = next((u for u in users if isinstance(u, dict) and u.get("email_hash") == email_hash), None)

    # First-time setup: create hashed admin entry
    if not admin_user:
        admin_user = {
            "name": "Administrator",
            "email_hash": email_hash,
            "password": generate_password_hash(password),
            "enabled": True,
            "login_history": []
        }
        users.append(admin_user)
        ok, resp = save_users(users)
        if not ok:
            log.error("Failed to save admin user: %s", resp)
            return "Server failed to save admin", 500
        session['simple_admin'] = True
        return redirect('/admin')

    # Verify password
    if check_password_hash(admin_user.get("password", ""), password):
        session['simple_admin'] = True
        return redirect('/admin')

    return "Wrong email or password", 403

# ---------------- Run ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False, use_reloader=False)
