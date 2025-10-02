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

GITHUB_API_BASE = "https://api.github.com"

# --- Single admin email enforcement ---
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
    # leave user-facing endpoints public
    public = {
        'login', 'register', 'index', 'get_users',
        'check_update', 'download_apk', 'get_apk',
        # admin login page and its POST must be accessible
        'admin_login_page', 'simplemind_login'
    }
    ep = request.endpoint
    if ep in public:
        return

    # otherwise require normal user session for site API calls
    if 'user_email' not in session:
        if request.path.startswith('/api') or request.is_json or request.path.startswith('/get_') or request.path.startswith('/login_analytics'):
            return jsonify({"success": False, "message": "Authentication required."}), 401
        return redirect(url_for('index'))

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
    # used by admin UI; must be admin (or could be extended to let users toggle themselves)
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
    # this returns login analytics for admin UI; allow public as JSON but only full data for admin
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
    apk_data = load_apk()
    if not apk_data.get("download_url"):
        return jsonify({"success": False, "message": "No APK available."}), 404
    r = requests.get(apk_data["download_url"], stream=True, timeout=30)
    if r.status_code != 200:
        return jsonify({"success": False, "message": "Failed to fetch APK"}), 500
    filename = apk_data.get("filename") or "app-latest.apk"
    return Response(r.iter_content(8192), content_type="application/vnd.android.package-archive",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})

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
    api_path = f"{APK_FOLDER}/{filename}"
    if not GITHUB_TOKEN:
        return jsonify({"success": False, "message": "Server missing GITHUB_TOKEN"}), 500
    url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{api_path}"
    data = {"message": f"Upload {filename}", "content": base64.b64encode(apk_bytes).decode(), "branch": BRANCH}
    r = requests.put(url, headers=gh_headers(), json=data, timeout=120)
    if r.status_code not in [200, 201]:
        return jsonify({"success": False, "message": f"GitHub upload failed {r.status_code}"}), 500
    sha = r.json().get("content", {}).get("sha") or (github_get_file_metadata(api_path) or {}).get("sha")
    download_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{BRANCH}/{APK_FOLDER}/{filename}"
    save_apk({"version": version, "changelog": f"Uploaded v{version}", "download_url": download_url, "filename": filename, "sha": sha})
    return jsonify({"success": True, "url": download_url})

@app.route('/delete_apk', methods=['POST'])
def delete_apk():
    admin_check = require_simple_admin_json()
    if admin_check:
        return admin_check
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
    save_apk({"version": None, "changelog": "", "download_url": "", "filename": None, "sha": None})
    return jsonify({"success": True})

@app.route('/check_update')
def check_update():
    apk_data = load_apk()
    return jsonify({
        "update_required": bool(apk_data.get("download_url")),
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

@app.route('/simplemind_login', methods=['POST'])
def simplemind_login():
    email_raw = request.form.get("email", "")
    password = request.form.get("password", "")
    if not email_raw or not password:
        return "Email and password required", 400

    email = email_raw.strip().lower()
    email_hash = hash_email(email)

    # Enforce single admin email if configured
    if ADMIN_EMAIL and email != ADMIN_EMAIL:
        return "Wrong email or password", 403

    password_ok = False

    # 1) If ADMIN_PASSWORD_HASH env var is set, prefer it
    if ADMIN_PASSWORD_HASH:
        try:
            if check_password_hash(ADMIN_PASSWORD_HASH, password):
                password_ok = True
        except Exception:
            password_ok = False

    # 2) Try to read admin.json from GitHub (preferred)
    admin_data = None
    try:
        admin_data = github_get_file('admin.json', None)
        # github_get_file returns None if not found (per your helper usage)
    except Exception:
        admin_data = None

    if admin_data:
        # If admin_data is found in repo, validate it
        stored_email_hash = admin_data.get('email_hash')
        if stored_email_hash and stored_email_hash != email_hash:
            return "Wrong email or password", 403
        stored_pass = admin_data.get('password', '')
        if stored_pass and check_password_hash(stored_pass, password):
            password_ok = True

    # 3) If not yet ok, allow first-time setup: create admin.json in repo (only if ADMIN_PASSWORD_HASH not set)
    if not password_ok and not ADMIN_PASSWORD_HASH and not admin_data:
        # create admin record and push to GitHub (preferred) or local fallback
        admin_record = {"email_hash": email_hash, "password": generate_password_hash(password)}
        # Try GitHub push if token exists
        if GITHUB_TOKEN:
            ok, resp = github_push_file('admin.json', json.dumps(admin_record, indent=2), "Create admin.json")
            if ok:
                password_ok = True
            else:
                # Push failed: fall back to local file and set password_ok if local write works
                log.error("Failed to write admin.json to GitHub: %s", resp)
                try:
                    with open('admin.json', 'w') as f:
                        json.dump(admin_record, f)
                    password_ok = True
                except Exception as e:
                    log.exception("Failed to write local admin.json: %s", e)
                    password_ok = False
        else:
            # No GITHUB_TOKEN -> local write fallback
            try:
                with open('admin.json', 'w') as f:
                    json.dump(admin_record, f)
                password_ok = True
            except Exception as e:
                log.exception("Failed to write local admin.json: %s", e)
                password_ok = False

    # 4) If still not ok, final rejection
    if not password_ok:
        return "Wrong email or password", 403

    # SUCCESS: set admin session and one-time allow_admin token
    session['simple_admin'] = True
    session['allow_admin'] = True
    return redirect('/admin-dashboard')


@app.route('/admin-dashboard')
def admin_dashboard():
    # require both flags: simple_admin and allow_admin (one-time)
    if not session.get('simple_admin') or not session.get('allow_admin'):
        session.pop('allow_admin', None)
        return redirect('/simplemindserverisgone')
    # consume allow token to prevent direct paste later
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

# ---------------- Run ----------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False
    )
