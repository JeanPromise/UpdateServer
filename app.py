# app.py (public admin.html, admin only via /simplemindserverisgone)
import base64
import json
import requests
import os
import logging
import hashlib
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

# ensure local folder exists as a safe fallback (harmless)
os.makedirs(APK_FOLDER, exist_ok=True)

GITHUB_API_BASE = "https://api.github.com"

# --- Single admin email enforcement (optional) ---
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
if ADMIN_EMAIL:
    ADMIN_EMAIL = ADMIN_EMAIL.strip().lower()
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH")  # optional hashed admin password

# --- Helper to hash email consistently ---
def hash_email(email: str) -> str:
    return hashlib.sha256(email.encode()).hexdigest()

# --- GitHub API Helpers ---
def gh_headers():
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "UpdateServer-App"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    return headers

def github_get_file(filename, default):
    url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{filename}?ref={BRANCH}"
    try:
        r = requests.get(url, headers=gh_headers(), timeout=20)
    except Exception:
        log.exception("GitHub GET exception for %s", filename)
        return default
    if r.status_code == 200:
        body = r.json()
        content = body.get("content", "")
        encoding = body.get("encoding", "base64")
        try:
            raw = base64.b64decode(content).decode() if encoding == "base64" else content
            return json.loads(raw)
        except Exception:
            log.exception("Failed to decode/parse %s", filename)
            return default
    return default

def github_get_file_metadata(filename):
    try:
        r = requests.get(f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{filename}?ref={BRANCH}", headers=gh_headers(), timeout=20)
        return r.json() if r.status_code == 200 else None
    except Exception:
        log.exception("metadata GET exception %s", filename)
    return None

def github_push_file(filename, content_str, message=None):
    if not GITHUB_TOKEN:
        return False, "GITHUB_TOKEN missing"
    url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{filename}"
    headers = gh_headers()
    r_get = requests.get(url, headers=headers, timeout=20)
    sha = r_get.json().get("sha") if r_get.status_code == 200 else None
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
    return (r.status_code in (200, 201), r.json() if r.status_code in (200, 201) else r.text)

# ---------------- Admin persistence helpers ----------------
def load_admin():
    """
    Try to load admin.json from GitHub (preferred). If not present or fails,
    fall back to a local admin.json file if it exists. Returns dict or None.
    """
    try:
        if GITHUB_TOKEN:
            data = github_get_file('admin.json', None)
            if isinstance(data, dict):
                return data
        # fallback to local file
        admin_json_path = 'admin.json'
        if os.path.exists(admin_json_path):
            with open(admin_json_path, 'r') as f:
                return json.load(f)
    except Exception:
        log.exception("load_admin failed")
    return None

def save_admin(admin_obj):
    """
    Save admin_obj (dict) to GitHub if token available; otherwise write local file.
    Returns (ok, resp) where ok is True on success.
    """
    try:
        content = json.dumps(admin_obj, indent=2)
        if GITHUB_TOKEN:
            ok, resp = github_push_file('admin.json', content, "Update admin.json")
            if ok:
                return True, resp
            log.error("github_push_file for admin.json failed: %s", resp)
        # fallback: write to local file
        admin_json_path = 'admin.json'
        with open(admin_json_path, 'w') as f:
            f.write(content)
        return True, "written-local"
    except Exception as e:
        log.exception("save_admin exception")
        return False, str(e)

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
    # endpoints allowed to proceed to their handler regardless of 'user_email' session
    public_endpoints = {
        'login', 'register', 'index', 'get_users',
        'check_update', 'download_apk', 'get_apk',
        # admin login page, its POST handler, and admin-dashboard MUST be allowed so their logic runs
        'admin_login_page', 'simplemind_login', 'admin_dashboard'
    }

    ep = request.endpoint

    # allow if endpoint is public
    if ep in public_endpoints:
        return

    # For API-ish calls (prefixes) return JSON auth error
    if 'user_email' not in session:
        if request.path.startswith('/api') or request.is_json or request.path.startswith('/get_') or request.path.startswith('/login_analytics'):
            return jsonify({"success": False, "message": "Authentication required."}), 401

        # for non-public pages requested without session, return 404 (do not redirect to index)
        return Response("Not found", status=404)

# ---------------- Pages ----------------
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

# ---------------- User Endpoints ----------------
@app.route('/register', methods=['POST'])
def register():
    data = request.get_json() or {}
    name, email, password = data.get('name'), data.get('email'), data.get('password')
    if not (name and email and password):
        return jsonify({"success": False, "message": "name, email, password required."}), 400
    users = load_users()
    if any(u.get('email') == email for u in users if isinstance(u, dict)):
        return jsonify({"success": False, "message": "Email already registered."})
    users.append({"name": name, "email": email, "password": generate_password_hash(password), "enabled": True, "login_history": []})
    ok, resp = save_users(users)
    return (jsonify({"success": True}) if ok else jsonify({"success": False, "message": resp}), 500)[not ok]

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    email, password = data.get('email'), data.get('password')
    users = load_users()
    for u in users:
        if u.get('email') == email:
            if not u.get('enabled', True):
                return jsonify({"success": False, "message": "User is disabled."})
            if check_password_hash(u.get('password'), password):
                session['user_email'] = email
                ip = request.headers.get('X-Forwarded-For', request.remote_addr)
                user_agent = request.headers.get("User-Agent", "")
                try:
                    country = requests.get(f"http://ip-api.com/json/{ip}", timeout=5).json().get("country", "Unknown")
                except Exception:
                    country = "Unknown"
                u.setdefault("login_history", []).append({
                    "timestamp": datetime.utcnow().isoformat(),
                    "ip": ip,
                    "country": country,
                    "user_agent": user_agent
                })
                save_users(users)
                return jsonify({"success": True})
            return jsonify({"success": False, "message": "Incorrect password."})
    return jsonify({"success": False, "message": "Email not registered."})

@app.route('/logout')
def logout():
    session.pop('user_email', None)
    session.pop('simple_admin', None)
    session.pop('allow_admin', None)
    return redirect(url_for('index'))

@app.route('/get_users')
def get_users():
    users = load_users()
    return jsonify([{k: v for k, v in u.items() if k != 'password'} for u in users if isinstance(u, dict)])

# ---------------- Admin-affecting endpoints (require simple_admin) ----------------
def require_simple_admin_json():
    if not session.get('simple_admin'):
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    return None

@app.route('/toggle_user', methods=['POST'])
def toggle_user():
    admin_check = require_simple_admin_json()
    if admin_check:
        return admin_check
    data = request.get_json() or {}
    email, enable = data.get('email'), data.get('enable', True)
    users = load_users()
    for u in users:
        if u.get('email') == email:
            u['enabled'] = bool(enable)
            save_users(users)
            return jsonify({"success": True})
    return jsonify({"success": False, "message": "User not found."}), 404

@app.route('/enable_all', methods=['POST'])
def enable_all():
    admin_check = require_simple_admin_json()
    if admin_check:
        return admin_check
    users = load_users()
    for u in users:
        u['enabled'] = True
    save_users(users)
    return jsonify({"success": True})

@app.route('/disable_all', methods=['POST'])
def disable_all():
    admin_check = require_simple_admin_json()
    if admin_check:
        return admin_check
    users = load_users()
    for u in users:
        u['enabled'] = False
    save_users(users)
    return jsonify({"success": True})

@app.route('/login_analytics')
def login_analytics():
    users = load_users()
    analytics = []
    for u in users:
        last = (u.get("login_history") or [])[-1] if u.get("login_history") else None
        analytics.append({
            "name": u.get("name"),
            "email": u.get("email"),
            "enabled": u.get("enabled"),
            "total_logins": len(u.get("login_history", [])),
            "last_login": last
        })
    return jsonify(analytics)

# ---------------- APK Endpoints (admin-only for uploads/deletes) ----------------
@app.route('/download_apk')
def download_apk():
    """
    Robust download endpoint:
      - Try serve local file in apks/ first (if filename set in apk.json and file exists).
      - Otherwise attempt to stream the download_url (GitHub raw or other).
      - Returns JSON 404 if neither available.
    """
    apk_data = load_apk()

    # 1) Try local file first (fast, offline-safe)
    filename = apk_data.get("filename")
    if filename:
        local_path = os.path.join(APK_FOLDER, filename)
        if os.path.exists(local_path):
            # serve local file
            try:
                return send_from_directory(APK_FOLDER, filename, as_attachment=True,
                                           mimetype="application/vnd.android.package-archive")
            except Exception:
                log.exception("Failed to send local APK file %s", local_path)

    # 2) Fallback: stream from download_url (existing behaviour)
    download_url = apk_data.get("download_url") or ""
    if download_url:
        try:
            r = requests.get(download_url, stream=True, timeout=30)
            if r.status_code == 200:
                out_filename = filename or "app-latest.apk"
                return Response(r.iter_content(8192),
                                content_type="application/vnd.android.package-archive",
                                headers={"Content-Disposition": f"attachment; filename={out_filename}"})
            else:
                log.warning("download_apk: remote returned status %s for %s", r.status_code, download_url)
        except Exception:
            log.exception("download_apk: exception when streaming remote url")

    # 3) Nothing available
    return jsonify({"success": False, "message": "No APK available or remote fetch failed."}), 404

@app.route('/upload_apk', methods=['POST'])
def upload_apk():
    admin_check = require_simple_admin_json()
    if admin_check:
        return admin_check
    if 'apk' not in request.files or 'version' not in request.form:
        return jsonify({"success": False, "message": "APK file and version required."}), 400

    file = request.files['apk']
    version = request.form['version'].strip()
    filename = secure_filename(f"app-v{version}.apk")
    apk_bytes = file.read()

    # 1) Save locally always (safe fallback)
    local_path = os.path.join(APK_FOLDER, filename)
    try:
        with open(local_path, 'wb') as f:
            f.write(apk_bytes)
    except Exception as e:
        log.exception("Failed to save local APK %s", local_path)
        return jsonify({"success": False, "message": f"Failed to save local APK: {e}"}), 500

    # 2) Try upload to GitHub if token present
    api_path = f"{APK_FOLDER}/{filename}"
    download_url = ""
    sha = None
    if GITHUB_TOKEN:
        url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{api_path}"
        data = {"message": f"Upload {filename}", "content": base64.b64encode(apk_bytes).decode(), "branch": BRANCH}
        try:
            r = requests.put(url, headers=gh_headers(), json=data, timeout=120)
            if r.status_code in [200, 201]:
                sha = r.json().get("content", {}).get("sha")
                download_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{BRANCH}/{APK_FOLDER}/{filename}"
            else:
                log.warning("GitHub upload returned %s: %s", r.status_code, r.text[:400])
        except Exception:
            log.exception("GitHub upload exception for %s", api_path)

    # If GitHub not used or upload failed, leave download_url empty and rely on local file
    apk_obj = {
        "version": version,
        "changelog": f"Uploaded v{version}",
        "download_url": download_url,
        "filename": filename,
        "sha": sha
    }

    # persist apk metadata (to GitHub if GITHUB_TOKEN, otherwise local via save_apk fallback)
    ok, resp = save_apk(apk_obj)
    if not ok:
        # still return success because local save succeeded; but inform admin
        log.error("save_apk failed: %s", resp)
        return jsonify({"success": True, "url": download_url, "message": "APK saved locally but metadata push failed."})

    return jsonify({"success": True, "url": download_url})

@app.route('/delete_apk', methods=['POST'])
def delete_apk():
    admin_check = require_simple_admin_json()
    if admin_check:
        return admin_check
    # Only reset metadata (do NOT delete local file automatically — safer)
    save_apk({"version": None, "changelog": "", "download_url": "", "filename": None, "sha": None})
    return jsonify({"success": True})

@app.route('/delete_apk_force', methods=['POST'])
def delete_apk_force():
    admin_check = require_simple_admin_json()
    if admin_check:
        return admin_check
    apk_data = load_apk()
    filename, sha = apk_data.get("filename"), apk_data.get("sha")
    if not filename:
        return jsonify({"success": False, "message": "No APK saved."}), 400
    if not sha:
        meta = github_get_file_metadata(f"{APK_FOLDER}/{filename}")
        sha = (meta or {}).get("sha")
    if not sha or not GITHUB_TOKEN:
        return jsonify({"success": False, "message": "Missing SHA or token."}), 400
    url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{APK_FOLDER}/{filename}"
    r = requests.delete(url, headers=gh_headers(), json={"message": f"Delete {filename}", "sha": sha, "branch": BRANCH}, timeout=30)
    if r.status_code not in [200, 204]:
        return jsonify({"success": False, "message": "GitHub delete failed."}), 500
    # Do NOT remove local file automatically to avoid accidental data loss; only reset metadata.
    save_apk({"version": None, "changelog": "", "download_url": "", "filename": None, "sha": None})
    return jsonify({"success": True})

@app.route('/check_update')
def check_update():
    apk_data = load_apk()
    return jsonify({
        "update_required": bool(apk_data.get("download_url")) or bool(apk_data.get("filename")),
        "apk_version": apk_data.get("version"),
        "url": apk_data.get("download_url")
    })

@app.route('/get_apk')
def get_apk():
    return jsonify(load_apk())

@app.route('/update_apk', methods=['POST'])
def update_apk():
    admin_check = require_simple_admin_json()
    if admin_check:
        return admin_check
    data = request.get_json() or {}
    save_apk(data)
    return jsonify({"success": True})

# ---------------- Admin Pages ----------------
@app.route('/simplemindserverisgone')
@app.route('/simplemindserverisgone.html')
def admin_login_page():
    return """
    <!DOCTYPE html>
    <html>
    <head><title>Admin Login</title></head>
    <body>
        <h2>Admin Login</h2>
        <form method="POST" action="/simplemind_login">
            <label>Email:</label><br>
            <input type="email" name="email" required><br>
            <label>Password:</label><br>
            <input type="password" name="password" required><br><br>
            <button type="submit">Login</button>
        </form>
    </body>
    </html>
    """

# Helper: find admin user in users list (by is_admin flag or by ADMIN_EMAIL)
def find_admin_in_users(users, email=None):
    for u in users:
        if isinstance(u, dict) and u.get('is_admin'):
            return u
    if email:
        for u in users:
            if isinstance(u, dict) and u.get('email', '').strip().lower() == email:
                return u
    if ADMIN_EMAIL:
        for u in users:
            if isinstance(u, dict) and u.get('email', '').strip().lower() == ADMIN_EMAIL:
                return u
    return None

@app.route('/simplemind_login', methods=['POST'])
def simplemind_login():
    email_raw = request.form.get("email", "")
    password = request.form.get("password", "")
    if not email_raw or not password:
        return "Email and password required", 400

    email = email_raw.strip().lower()

    # enforce ADMIN_EMAIL if configured
    if ADMIN_EMAIL and email != ADMIN_EMAIL:
        return "Wrong email or password", 403

    # 1) prefer admin.json if present (GitHub or local via load_admin)
    admin_data = load_admin()
    if admin_data:
        try:
            stored_hash = admin_data.get('password', '')
            stored_email_hash = admin_data.get('email_hash', '')
            if stored_email_hash and stored_email_hash != hash_email(email):
                return "Wrong email or password", 403
            if stored_hash and check_password_hash(stored_hash, password):
                session['simple_admin'] = True
                session['allow_admin'] = True
                return redirect('/admin-dashboard')
            return "Wrong email or password", 403
        except Exception:
            log.exception("checking admin_data failed")

    # 2) check users.json for a user marked is_admin (or matches ADMIN_EMAIL)
    users = load_users()
    admin_user = find_admin_in_users(users, email=email)

    if admin_user:
        stored_email = admin_user.get('email', '').strip().lower()
        if stored_email != email:
            return "Wrong email or password", 403
        stored_pass = admin_user.get('password', '')
        if stored_pass and check_password_hash(stored_pass, password):
            session['simple_admin'] = True
            session['allow_admin'] = True
            return redirect('/admin-dashboard')
        return "Wrong email or password", 403

    # 3) no admin found anywhere -> first-time setup: add admin to users.json AND save admin.json
    updated = False
    for u in users:
        if isinstance(u, dict) and u.get('email', '').strip().lower() == email:
            u['password'] = generate_password_hash(password)
            u['is_admin'] = True
            u.setdefault('enabled', True)
            u.setdefault('login_history', [])
            updated = True
            break

    if not updated:
        new_admin = {
            "name": "Admin",
            "email": email,
            "password": generate_password_hash(password),
            "enabled": True,
            "login_history": [],
            "is_admin": True
        }
        users.append(new_admin)

    ok, resp = save_users(users)
    if not ok:
        log.error("Failed to persist admin user to users.json: %s", resp)
        return "Server error saving admin", 500

    # Also persist admin.json as a direct admin record so it's always available
    # 3) no admin found anywhere -> first-time setup: create admin.json only (do NOT touch users.json)
    admin_record = {"email_hash": hash_email(email), "password": generate_password_hash(password)}
    ok2, resp2 = save_admin(admin_record)
    if not ok2:
        log.error("Failed to persist admin.json: %s", resp2)
        return "Server error saving admin", 500

    session['simple_admin'] = True
    session['allow_admin'] = True
    return redirect('/admin-dashboard')

@app.route('/admin-dashboard')
def admin_dashboard():
    # require both flags: simple_admin and allow_admin (one-time)
    if not session.get('simple_admin') or not session.get('allow_admin'):
        session.pop('allow_admin', None)
        # Not allowed: send user to admin login page (not index), per your requirement
        return redirect('/simplemindserverisgone')
    # consume allow token so direct paste later won't work
    session.pop('allow_admin', None)

    admin_file_path = os.path.join(os.getcwd(), 'admin.html')
    if not os.path.exists(admin_file_path):
        return "Admin file missing", 404
    with open(admin_file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return Response(content, mimetype='text/html')

# Block direct access to admin.html or /admin
@app.route('/admin')
@app.route('/admin.html')
def block_admin_direct():
    return "Forbidden", 403

# ---------------- Admin helper endpoints (search/delete) ----------------
@app.route('/admin_search_users', methods=['GET'])
def admin_search_users():
    if not session.get('simple_admin'):
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    query = request.args.get('q', '').lower()
    users = load_users()
    filtered = [
        {k: v for k, v in u.items() if k != 'password'}
        for u in users
        if isinstance(u, dict) and (query in u.get('name', '').lower() or query in u.get('email', '').lower())
    ]
    return jsonify(filtered)

@app.route('/admin_delete_user', methods=['POST'])
def admin_delete_user():
    if not session.get('simple_admin'):
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    data = request.get_json() or {}
    email = data.get('email')
    if not email:
        return jsonify({"success": False, "message": "Email required"}), 400
    users = load_users()
    new_users = [u for u in users if u.get('email') != email]
    if len(new_users) == len(users):
        return jsonify({"success": False, "message": "User not found"}), 404
    ok, resp = save_users(new_users)
    return (jsonify({"success": True}) if ok else jsonify({"success": False, "message": resp}), 500)[not ok]

@app.route('/appstore')
@app.route('/appstore.html')
def appstore():
    apk_data = load_apk()
    apps = []
    if apk_data.get("download_url") or apk_data.get("filename"):
        # map filename into friendly app name
        fname = apk_data.get("filename", "app-latest.apk")
        # for now hardcode Tomorrow Entertainment as first app
        app_name = "Tomorrow Entertainment"
        # use download_url if present (GitHub raw link), otherwise use local download endpoint
        url = apk_data.get("download_url") or url_for('download_apk', _external=True)
        apps.append({
            "name": app_name,
            "version": apk_data.get("version") or "N/A",
            "url": url,
            "filename": fname
        })

    html = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8">
      <meta name="viewport" content="width=device-width, initial-scale=1.0">
      <title>My App Store</title>
      <style>
        body {
          background-color: #121212;
          color: #fff;
          font-family: Arial, sans-serif;
          margin: 0;
          padding: 20px;
        }
        h1 {
          text-align: center;
          margin-bottom: 20px;
        }
        .app-card {
          background: #1e1e1e;
          border-radius: 8px;
          padding: 15px;
          margin: 10px 0;
          box-shadow: 0 0 8px rgba(0,0,0,0.5);
        }
        .app-name {
          font-size: 18px;
          font-weight: bold;
        }
        .app-version {
          color: #aaa;
          font-size: 14px;
        }
        .download-btn {
          display: inline-block;
          margin-top: 10px;
          padding: 8px 16px;
          background: #2196f3;
          color: #fff;
          border-radius: 5px;
          text-decoration: none;
        }
        .download-btn:hover {
          background: #1976d2;
        }
      </style>
    </head>
    <body>
      <h1>My App Store</h1>
    """

    if not apps:
        html += "<p>No apps available yet.</p>"
    else:
        for app in apps:
            html += f"""
            <div class="app-card">
              <div class="app-name">{app['name']}</div>
              <div class="app-version">Version: {app['version']}</div>
              <a class="download-btn" href="{app['url']}">Download</a>
            </div>
            """

    html += """
    </body>
    </html>
    """
    return Response(html, mimetype="text/html")

# NEW direct link route
@app.route('/x.apk')
def direct_apk_download():
    apk_data = load_apk()
    # redirect to raw GitHub link if available, otherwise to local download
    if apk_data.get("download_url"):
        return redirect(apk_data["download_url"])
    if apk_data.get("filename"):
        return redirect(url_for('download_apk'))
    return "No APK found", 404

# ---------------- Run ----------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False
    )
