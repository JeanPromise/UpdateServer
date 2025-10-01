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
# Set ADMIN_EMAIL (case-insensitive) in environment to lock admin to a single email.
# Optionally set ADMIN_PASSWORD_HASH (a werkzeug hashed password string) to avoid using admin.json.
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")  # e.g. "admin@example.com"
if ADMIN_EMAIL:
    ADMIN_EMAIL = ADMIN_EMAIL.strip().lower()
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH")  # optional: hashed password (generate_password_hash on your password)

# --- Helper to hash email consistently ---
def hash_email(email: str) -> str:
    return hashlib.sha256(email.encode()).hexdigest()

# --- GitHub API Helpers ---
def gh_headers():
    headers = {
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "UpdateServer-App"
    }
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
            if encoding == "base64":
                raw = base64.b64decode(content).decode()
            else:
                raw = content
            return json.loads(raw)
        except Exception:
            log.exception("Failed to decode/parse %s", filename)
            return default

    log.debug("GitHub GET %s returned %s", filename, r.status_code)
    return default

def github_get_file_metadata(filename):
    url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{filename}?ref={BRANCH}"
    try:
        r = requests.get(url, headers=gh_headers(), timeout=20)
        if r.status_code == 200:
            return r.json()
        log.debug("metadata GET %s returned %s", filename, r.status_code)
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

    if r.status_code in (200, 201):
        return True, r.json()
    else:
        log.error("GitHub PUT failed %s: %s", r.status_code, r.text[:500])
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
    # Keep the regular site (index/login/register/etc.) public
    public = {
        'login', 'register', 'index', 'get_users',
        'check_update', 'download_apk', 'get_apk',
        # admin login page and its POST must be accessible publicly
        'admin_login_page', 'simplemind_login'
    }
    ep = request.endpoint
    if ep in public:
        return

    # normal user session enforcement (unchanged)
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

@app.route('/toggle_user', methods=['POST'])
def toggle_user():
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

# ---------------- Login Analytics ----------------
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

# ---------------- APK Endpoints ----------------
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
    sha = r.json().get("content", {}).get("sha") or github_get_file_metadata(api_path).get("sha")
    download_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{BRANCH}/{APK_FOLDER}/{filename}"
    save_apk({"version": version, "changelog": f"Uploaded v{version}", "download_url": download_url, "filename": filename, "sha": sha})
    return jsonify({"success": True, "url": download_url})

@app.route('/delete_apk', methods=['POST'])
def delete_apk():
    save_apk({"version": None, "changelog": "", "download_url": "", "filename": None, "sha": None})
    return jsonify({"success": True})

@app.route('/delete_apk_force', methods=['POST'])
def delete_apk_force():
    apk_data = load_apk()
    filename, sha = apk_data.get("filename"), apk_data.get("sha")
    if not filename:
        return jsonify({"success": False, "message": "No APK saved."}), 400
    if not sha:
        sha = github_get_file_metadata(f"{APK_FOLDER}/{filename}").get("sha")
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
    data = request.get_json() or {}
    save_apk(data)
    return jsonify({"success": True})

# ---------------- Admin Pages ----------------
# Serve admin login page on both paths; this is the ONLY way to reach admin login
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

    # Enforce single admin email if ADMIN_EMAIL is set
    if ADMIN_EMAIL:
        if email != ADMIN_EMAIL:
            return "Wrong email or password", 403

    # Password verification order:
    # 1) If ADMIN_PASSWORD_HASH env var is set, check against it (recommended)
    # 2) Else check admin.json if present (legacy)
    # 3) Else allow first-time setup: create admin.json with provided password (only if ADMIN_EMAIL matches or ADMIN_EMAIL not set)
    password_ok = False

    if ADMIN_PASSWORD_HASH:
        # ADMIN_PASSWORD_HASH should be a werkzeug generate_password_hash string
        try:
            if check_password_hash(ADMIN_PASSWORD_HASH, password):
                password_ok = True
        except Exception:
            password_ok = False

    admin_json_path = 'admin.json'
    admin_data = None
    if not password_ok and os.path.exists(admin_json_path):
        try:
            with open(admin_json_path, 'r') as f:
                admin_data = json.load(f)
            # If admin.json stores email_hash, ensure it matches (extra safety)
            stored_email_hash = admin_data.get('email_hash')
            if stored_email_hash and stored_email_hash != email_hash:
                # email doesn't match stored admin
                return "Wrong email or password", 403
            stored_pass = admin_data.get('password', '')
            if stored_pass and check_password_hash(stored_pass, password):
                password_ok = True
        except Exception:
            password_ok = False

    # If still not ok and no ADMIN_PASSWORD_HASH and admin.json missing -> first-time setup
    if not password_ok and not ADMIN_PASSWORD_HASH and not os.path.exists(admin_json_path):
        # allow creating admin.json only if either ADMIN_EMAIL is unset or matches the provided email
        if ADMIN_EMAIL and email != ADMIN_EMAIL:
            return "Wrong email or password", 403
        try:
            admin_record = {
                "email_hash": email_hash,
                "password": generate_password_hash(password)
            }
            with open(admin_json_path, 'w') as f:
                json.dump(admin_record, f)
            password_ok = True
        except Exception:
            password_ok = False

    if not password_ok:
        return "Wrong email or password", 403

    # Successful admin login -> set admin session + one-time allow token and redirect to admin-dashboard
    session['simple_admin'] = True
    session['allow_admin'] = True
    return redirect('/admin-dashboard')

@app.route('/admin-dashboard')
def admin_dashboard():
    # require both flags: logged-in admin and one-time allow token
    if not session.get('simple_admin') or not session.get('allow_admin'):
        # consume token if any and redirect to admin login page only
        session.pop('allow_admin', None)
        return redirect('/simplemindserverisgone')

    # consume allow token immediately so direct pasting won't grant access
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
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
