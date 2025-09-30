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
    Flask, request, jsonify, send_from_directory,
    session, redirect, url_for, abort, render_template
)
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
    if not session.get('simple_admin'):
        return redirect('/simplemindserverisgone')
    # ✅ serve admin.html (your only admin file)
    return send_from_directory('.', 'admin.html')

@app.route('/admin.html')
def block_direct_admin():
    return "Not today buddy", 403

# ---------------- Simplemind Login Page ----------------
@app.route('/simplemindserverisgone')
def simplemindserverisgone():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Secure Admin Login</title>
    </head>
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

# ---------------- Login Logic ----------------
@app.route('/simplemind_login', methods=['POST'])
def simplemind_login():
    email = request.form.get("email")
    password = request.form.get("password")

    if not email or not password:
        return "Email and password required", 400

    email_hash = hash_email(email.strip().lower())
    users = load_users()
    admin_user = next((u for u in users if u.get("email_hash") == email_hash), None)

    if not admin_user:
        # First-time setup
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

    return "Wrong email or password", 403

# ---------------- Run ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)),
            debug=False, use_reloader=False)
