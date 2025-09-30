# app.py (public admin.html, no login redirect)
import base64
import json
import requests
import os
import logging
from werkzeug.security import generate_password_hash, check_password_hash
import hashlib

# Helper to hash email in a stable way
def hash_email(email: str) -> str:
    return hashlib.sha256(email.encode()).hexdigest()

from flask import (
    Flask, request, jsonify, send_from_directory, Response,
    session, redirect, url_for, abort, render_template
)
from werkzeug.utils import secure_filename
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
            return json.loads(raw)
        except Exception as e:
            log.exception("Failed to decode/parse %s: %s", filename, e)
            return default

    log.warning("GitHub GET %s returned %s: %s", filename, r.status_code, r.text[:200])
    return default

def github_get_file_metadata(filename):
    url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{filename}?ref={BRANCH}"
    try:
        r = requests.get(url, headers=gh_headers(), timeout=20)
        if r.status_code == 200:
            return r.json()
        log.warning("metadata GET %s returned %s", filename, r.status_code)
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
    public = {
        'login', 'register', 'index', 'admin',
        'get_users', 'check_update', 'download_apk',
        'get_apk', 'simplemindserverisgone',
        'simplemind_login'
    }
    ep = request.endpoint
    if ep in public:
        return
    if 'user_email' not in session:
        if request.path.startswith('/api') or request.is_json or request.path.startswith('/get_') or request.path.startswith('/login_analytics'):
            return jsonify({"success": False, "message": "Authentication required."}), 401
        return redirect(url_for('index'))

# ---------------- Pages ----------------
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

# ---------------- Admin Restriction ----------------
ALLOWED_ADMIN_PATH = "https://tomorrow-au2q.onrender.com//simplemindserverisgone"

@app.route('/admin')
def admin_dashboard():
    # Strict URL enforcement
    if request.url != ALLOWED_ADMIN_PATH:
        abort(403)
    if not session.get('simple_admin'):
        return redirect('/simplemindserverisgone')
    return render_template("admin_dashboard.html")

@app.route('/admin.html')
def block_direct_admin():
    return "Not today buddy", 403

# ---------------- User Endpoints ----------------
# (register, login, logout, toggle, enable_all, disable_all, analytics, apk upload/download)
# ... your existing endpoints unchanged ...

# ---------------- Simple Admin Gate ----------------
@app.route('/simplemindserverisgone')
def simplemindserverisgone():
    return send_from_directory('.', 'simplemindserverisgone.html')

@app.route('/simplemind_login', methods=['POST'])
def simplemind_login():
    password = request.form.get("password")
    email = "admin"  # fixed admin identity, but only hash is stored
    email_hash = hash_email(email)

    users = load_users()
    admin_user = next((u for u in users if u.get("email_hash") == email_hash), None)

    if not admin_user:
        admin_user = {
            "name": "Administrator",
            "email_hash": email_hash,
            "password": generate_password_hash(password),
            "enabled": True,
            "login_history": []
        }
        users.append(admin_user)
        save_users(users)
        session['simple_admin'] = True
        return redirect('/admin')

    if check_password_hash(admin_user.get("password", ""), password):
        session['simple_admin'] = True
        return redirect('/admin')

    return "Wrong password", 403

# ---------------- Run ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)),
            debug=False, use_reloader=False)
