# app.py
import threading
import time
import random
import base64
import json
import requests
import os
import logging
import hashlib
import sqlite3
from datetime import datetime, timedelta
from flask import (
    Flask, request, jsonify, send_from_directory, Response, session, redirect, url_for
)
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from flask import Response, url_for
from flask import send_file

# --- Basic logging ---
logging.basicConfig(level=logging.INFO)
log = logging.getLogger("UpdateServer")

# Serve static files from same folder as app.py
app = Flask(__name__, static_url_path='', static_folder='.')
app.secret_key = os.getenv("FLASK_SECRET_KEY", "supersecretkey")

# --- Defaults to help keep the app awake when editing on GitHub ---
os.environ.setdefault("SELF_URL", "https://tomorrow-au2q.onrender.com")
os.environ.setdefault("KEEPALIVE_ENABLED", "true")
os.environ.setdefault("KEEPALIVE_INTERVAL", "30")

log.info("UpdateServer initialized at %s", datetime.utcnow().isoformat())

# ---------------- Config ----------------
GITHUB_OWNER = "JeanPromise"
GITHUB_REPO = "UpdateServer"
BRANCH = "main"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")

USERS_FILE = "users.json"
APK_FILE = "apk.json"
APK_FOLDER = "apks"

os.makedirs(APK_FOLDER, exist_ok=True)

GITHUB_API_BASE = "https://api.github.com"

DEFAULT_COMMISSION = float(os.getenv("DEFAULT_COMMISSION", "0.10"))
BONUS_COMMISSION   = float(os.getenv("BONUS_COMMISSION", "0.15"))
BONUS_THRESHOLD    = int(os.getenv("BONUS_THRESHOLD", "5"))

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL")
if ADMIN_EMAIL:
    ADMIN_EMAIL = ADMIN_EMAIL.strip().lower()
ADMIN_PASSWORD_HASH = os.getenv("ADMIN_PASSWORD_HASH")

def hash_email(email: str) -> str:
    return hashlib.sha256(email.encode()).hexdigest()

def gh_headers():
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "UpdateServer-App"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    return headers

def github_get_file(filename, default):
    url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{filename}?ref={BRANCH}"
    try:
        r = requests.get(url, headers=gh_headers(), timeout=20)
        if r.status_code == 200:
            body = r.json()
            content = body.get("content", "")
            encoding = body.get("encoding", "base64")
            try:
                raw = base64.b64decode(content).decode() if encoding == "base64" else content
                return json.loads(raw)
            except Exception:
                log.exception("Failed to decode/parse %s from GitHub", filename)
        else:
            log.warning("github_get_file: non-200 status %s for %s", r.status_code, filename)
    except Exception:
        log.exception("GitHub GET exception for %s", filename)

    try:
        if os.path.exists(filename):
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        log.exception("Local fallback read failed for %s", filename)

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
    try:
        r_get = requests.get(url, headers=headers, timeout=20)
        sha = r_get.json().get("sha") if r_get.status_code == 200 else None
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
    return (r.status_code in (200, 201), r.json() if r.status_code in (200, 201) else r.text)

def github_push_binary(filename, binary_bytes, message=None):
    if not GITHUB_TOKEN:
        return False, "GITHUB_TOKEN missing"
    url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{filename}"
    headers = gh_headers()
    try:
        r_get = requests.get(url, headers=headers, timeout=20)
        sha = r_get.json().get("sha") if r_get.status_code == 200 else None
    except Exception:
        sha = None

    payload = {
        "message": message or f"Update {filename} at {datetime.utcnow().isoformat()}",
        "content": base64.b64encode(binary_bytes).decode(),
        "branch": BRANCH
    }
    if sha:
        payload["sha"] = sha
    try:
        r = requests.put(url, headers=headers, json=payload, timeout=120)
    except Exception as e:
        log.exception("GitHub PUT exception for binary %s", filename)
        return False, str(e)
    return (r.status_code in (200, 201), r.json() if r.status_code in (200, 201) else r.text)

def load_admin():
    try:
        data = github_get_file('admin.json', None)
        if isinstance(data, dict):
            return data
    except Exception:
        log.exception("load_admin failed")
    return None

def save_admin(admin_obj):
    try:
        content = json.dumps(admin_obj, indent=2)
        if GITHUB_TOKEN:
            ok, resp = github_push_file('admin.json', content, "Update admin.json")
            if ok:
                return True, resp
            log.error("github_push_file for admin.json failed: %s", resp)
        admin_json_path = 'admin.json'
        with open(admin_json_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return True, "written-local"
    except Exception as e:
        log.exception("save_admin exception")
        return False, str(e)

def load_users():
    default = []
    data = github_get_file(USERS_FILE, default)
    return data if isinstance(data, list) else default

def save_users(users_list):
    try:
        content = json.dumps(users_list, indent=2)
        if GITHUB_TOKEN:
            ok, resp = github_push_file(USERS_FILE, content, "Update users")
            if ok:
                return True, resp
            log.error("github_push_file for users.json failed: %s", resp)
        with open(USERS_FILE, 'w', encoding='utf-8') as f:
            f.write(content)
        return True, "written-local"
    except Exception as e:
        log.exception("save_users exception")
        return False, str(e)

def load_apk():
    default = {"version": None, "changelog": "", "download_url": "", "filename": None, "sha": None}
    data = github_get_file(APK_FILE, default)
    if not isinstance(data, dict):
        return default
    for k in default:
        data.setdefault(k, default[k])
    return data

def save_apk(apk_obj):
    try:
        content = json.dumps(apk_obj, indent=2)
        if GITHUB_TOKEN:
            ok, resp = github_push_file(APK_FILE, content, "Update APK data")
            if ok:
                return True, resp
            log.error("github_push_file for apk.json failed: %s", resp)
        with open(APK_FILE, 'w', encoding='utf-8') as f:
            f.write(content)
        return True, "written-local"
    except Exception as e:
        log.exception("save_apk exception")
        return False, str(e)

DB_PATH = "sales.db"

def init_db():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            c = conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS sales (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_email TEXT NOT NULL,
                    product TEXT NOT NULL,
                    price REAL NOT NULL,
                    currency TEXT NOT NULL,
                    usd_value REAL,
                    commission_rate REAL,
                    commission_amount REAL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    approved_at TEXT
                )
            """)
            conn.commit()
        log.info("Initialized SQLite DB at %s", DB_PATH)
    except Exception:
        log.exception("Failed to initialize DB")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES|sqlite3.PARSE_COLNAMES)
    conn.row_factory = sqlite3.Row
    return conn

def push_sales_db_to_github():
    if not os.path.exists(DB_PATH):
        return False, "DB missing"
    try:
        with open(DB_PATH, 'rb') as f:
            data = f.read()
        if GITHUB_TOKEN:
            ok, resp = github_push_binary('sales.db', data, f"Update sales.db at {datetime.utcnow().isoformat()}")
            if ok:
                log.info("Pushed sales.db to GitHub")
                return True, resp
            else:
                log.error("Failed to push sales.db to GitHub: %s", resp)
                return False, resp
        else:
            return True, "local-only"
    except Exception:
        log.exception("push_sales_db_to_github failed")
        return False, "exception"

# ---------------- Public/Private Enforcement ----------------
@app.before_request
def require_login():
    public_endpoints = {
        'login', 'register', 'index', 'day_page', 'get_users',
        'check_update', 'download_apk', 'get_apk',
        'admin_login_page', 'simplemind_login', 'admin_dashboard',
        'mysales_page', 'goodday_page'  # allow the mysales and goodday endpoints
    }

    ep = request.endpoint
    if ep in public_endpoints:
        return

    if session.get('simple_admin'):
        return

    if 'user_email' not in session:
        if request.path.startswith('/api') or request.is_json or request.path.startswith('/get_') or request.path.startswith('/login_analytics'):
            return jsonify({"success": False, "message": "Authentication required."}), 401
        return Response("Not found", status=404)

# ---------------- Pages ----------------
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/day')
def day_page():
    return send_from_directory('.', 'day.html')

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
    users.append({"name": name, "email": email, "password": generate_password_hash(password), "enabled": True, "login_history": []})
    ok, resp = save_users(users)
    if ok:
        return jsonify({"success": True})
    log.error("Failed to save users on register: %s", resp)
    return jsonify({"success": False, "message": "Failed to persist user data."}), 500

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
                    country = requests.get(f"http://ip-api.com/json/{ip}", timeout=5).json().get("country", "Unknown")
                except Exception:
                    country = "Unknown"
                u.setdefault("login_history", []).append({
                    "timestamp": datetime.utcnow().isoformat(),
                    "ip": ip,
                    "country": country,
                    "user_agent": user_agent
                })
                ok, resp = save_users(users)
                if not ok:
                    log.error("Failed to persist login history for %s: %s", email, resp)
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
    found = False
    for u in users:
        if isinstance(u, dict) and u.get('email') == email:
            u['enabled'] = bool(enable)
            found = True
            break
    if not found:
        return jsonify({"success": False, "message": "User not found."}), 404
    ok, resp = save_users(users)
    if not ok:
        log.error("toggle_user: failed to save users: %s", resp)
        return jsonify({"success": False, "message": "Failed to persist user changes."}), 500
    return jsonify({"success": True})

@app.route('/enable_all', methods=['POST'])
def enable_all():
    admin_check = require_simple_admin_json()
    if admin_check:
        return admin_check
    users = load_users()
    for u in users:
        if isinstance(u, dict):
            u['enabled'] = True
    ok, resp = save_users(users)
    if not ok:
        log.error("enable_all: failed to save users: %s", resp)
        return jsonify({"success": False, "message": "Failed to persist changes."}), 500
    return jsonify({"success": True})

@app.route('/disable_all', methods=['POST'])
def disable_all():
    admin_check = require_simple_admin_json()
    if admin_check:
        return admin_check
    users = load_users()
    for u in users:
        if isinstance(u, dict):
            u['enabled'] = False
    ok, resp = save_users(users)
    if not ok:
        log.error("disable_all: failed to save users: %s", resp)
        return jsonify({"success": False, "message": "Failed to persist changes."}), 500
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
    apk_data = load_apk()
    filename = apk_data.get("filename")
    if filename:
        local_path = os.path.join(APK_FOLDER, filename)
        if os.path.exists(local_path):
            try:
                return send_from_directory(APK_FOLDER, filename, as_attachment=True,
                                           mimetype="application/vnd.android.package-archive")
            except Exception:
                log.exception("Failed to send local APK file %s", local_path)

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

    local_path = os.path.join(APK_FOLDER, filename)
    try:
        with open(local_path, 'wb') as f:
            f.write(apk_bytes)
    except Exception as e:
        log.exception("Failed to save local APK %s", local_path)
        return jsonify({"success": False, "message": f"Failed to save local APK: {e}"}), 500

    api_path = f"{APK_FOLDER}/{filename}"
    download_url = ""
    sha = None
    github_ok = False
    if GITHUB_TOKEN:
        url = f"{GITHUB_API_BASE}/repos/{GITHUB_OWNER}/{GITHUB_REPO}/contents/{api_path}"
        data = {"message": f"Upload {filename}", "content": base64.b64encode(apk_bytes).decode(), "branch": BRANCH}
        try:
            r = requests.put(url, headers=gh_headers(), json=data, timeout=120)
            if r.status_code in [200, 201]:
                sha = r.json().get("content", {}).get("sha")
                download_url = f"https://raw.githubusercontent.com/{GITHUB_OWNER}/{GITHUB_REPO}/{BRANCH}/{APK_FOLDER}/{filename}"
                github_ok = True
            else:
                log.warning("GitHub upload returned %s: %s", r.status_code, r.text[:400])
        except Exception:
            log.exception("GitHub upload exception for %s", api_path)

    if not github_ok:
        try:
            download_url = url_for('download_apk', _external=True)
        except Exception:
            download_url = ""

    apk_obj = {
        "version": version,
        "changelog": f"Uploaded v{version}",
        "download_url": download_url,
        "filename": filename,
        "sha": sha
    }

    ok, resp = save_apk(apk_obj)
    if not ok:
        log.error("save_apk failed: %s", resp)
        return jsonify({"success": True, "url": download_url, "message": "APK saved locally but metadata push failed."})

    return jsonify({"success": True, "url": download_url})

# ---------------- New Page ----------------
@app.route('/goodday')
@app.route('/goodday.html')
def goodday_page():
    # mirror index/day behavior and rely on current working dir
    local_file = os.path.join(os.getcwd(), 'goodday.html')
    if os.path.exists(local_file):
        try:
            return send_from_directory('.', 'goodday.html')
        except Exception:
            log.exception("send_from_directory failed for goodday.html")
    return Response("""
<!doctype html>
<html><head><meta charset="utf-8"/><title>Good Day</title></head>
<body>
<h3>Good Day Page (Fallback)</h3>
<p>Put <code>goodday.html</code> next to <code>app.py</code> to see the full page.</p>
</body></html>
""", mimetype='text/html')


@app.route('/delete_apk', methods=['POST'])
def delete_apk():
    admin_check = require_simple_admin_json()
    if admin_check:
        return admin_check
    ok, resp = save_apk({"version": None, "changelog": "", "download_url": "", "filename": None, "sha": None})
    if not ok:
        log.error("delete_apk: failed to persist apk reset: %s", resp)
        return jsonify({"success": False, "message": "Failed to persist APK metadata."}), 500
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
    ok, resp = save_apk({"version": None, "changelog": "", "download_url": "", "filename": None, "sha": None})
    if not ok:
        log.error("delete_apk_force: failed to persist apk reset: %s", resp)
        return jsonify({"success": False, "message": "Failed to persist APK metadata."}), 500
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

# ===== Keepalive (safe) =====
@app.route('/_fake_ping', methods=['GET', 'POST'])
def fake_ping():
    data = request.get_json(silent=True) or {}
    record = {
        "timestamp": datetime.utcnow().isoformat(),
        "fake": True,
        "payload": data
    }
    try:
        path = 'keepalive.json'
        existing = []
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    existing = json.load(f)
            except Exception:
                existing = []
        existing.append(record)
        existing = existing[-100:]
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(existing, f, indent=2)
        except Exception:
            log.exception("keepalive write failed")
    except Exception:
        log.exception("keepalive top-level failure")

    return jsonify({"success": True, "recorded": record})

def _keepalive_worker(ping_url, interval_seconds, fake_profiles):
    log.info("Keepalive worker started: url=%s interval=%ss", ping_url, interval_seconds)
    while True:
        try:
            profile = random.choice(fake_profiles)
            payload = {
                "name": profile.get("name"),
                "email": profile.get("email"),
                "note": "keepalive",
                "ts": datetime.utcnow().isoformat()
            }
            headers = {"User-Agent": profile.get("ua", "KeepAliveBot/1.0")}
            try:
                requests.post(ping_url, json=payload, headers=headers, timeout=8)
                log.debug("Keepalive ping sent payload=%s", payload)
            except Exception:
                log.exception("Keepalive ping post failed")
        except Exception:
            log.exception("Keepalive worker exception")
        time.sleep(interval_seconds)

_keepalive_started = False

def start_keepalive():
    global _keepalive_started
    if _keepalive_started:
        return
    try:
        KEEPALIVE_ENABLED = os.getenv("KEEPALIVE_ENABLED", "true").lower() in ("1", "true", "yes")
        if not KEEPALIVE_ENABLED:
            log.info("Keepalive disabled by env")
            _keepalive_started = True
            return

        KEEPALIVE_INTERVAL = int(os.getenv("KEEPALIVE_INTERVAL", "30"))
        SELF_URL = os.getenv("SELF_URL")
        if SELF_URL:
            ping_url = SELF_URL.rstrip('/') + '/_fake_ping'
        else:
            port = os.getenv("PORT", "5000")
            ping_url = f"http://127.0.0.1:{port}/_fake_ping"

        fake_profiles = [
            {"name": "Visitor One", "email": "visitor1@local", "ua": "KeepAliveBot/1.0"},
            {"name": "Visitor Two", "email": "visitor2@local", "ua": "KeepAliveBot/1.1"},
            {"name": "Ghost User", "email": "ghost@local", "ua": "KeepAliveBot/1.2"},
        ]

        t = threading.Thread(
            target=_keepalive_worker,
            args=(ping_url, KEEPALIVE_INTERVAL, fake_profiles),
            daemon=True
        )
        t.start()
        _keepalive_started = True
        log.info("Keepalive thread started (ping_url=%s)", ping_url)
    except Exception:
        log.exception("Failed to start keepalive thread")
        _keepalive_started = True

with app.app_context():
    start_keepalive()
    try:
        init_db()
    except Exception:
        log.exception("init_db call failed at startup")

# ===== End Keepalive =====

@app.route('/update_apk', methods=['POST'])
def update_apk():
    admin_check = require_simple_admin_json()
    if admin_check:
        return admin_check
    data = request.get_json() or {}
    ok, resp = save_apk(data)
    if not ok:
        log.error("update_apk: failed to persist apk: %s", resp)
        return jsonify({"success": False, "message": "Failed to persist APK metadata."}), 500
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

    if ADMIN_EMAIL and email != ADMIN_EMAIL:
        return "Wrong email or password", 403

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
    if not session.get('simple_admin') or not session.get('allow_admin'):
        session.pop('allow_admin', None)
        return redirect('/simplemindserverisgone')
    session.pop('allow_admin', None)
    admin_file_path = os.path.join(os.getcwd(), 'admin.html')
    if not os.path.exists(admin_file_path):
        return "Admin file missing", 404
    with open(admin_file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    return Response(content, mimetype='text/html')

@app.route('/admin')
@app.route('/admin.html')
def block_admin_direct():
    return "Forbidden", 403

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
    email = data.get("email")
    if not email:
        return jsonify({"success": False, "message": "Email required"}), 400

    users = load_users()
    new_users = [u for u in users if isinstance(u, dict) and u.get('email') != email]

    if len(new_users) == len(users):
        return jsonify({"success": False, "message": "User not found"}), 404

    ok, resp = save_users(new_users)
    if not ok:
        return jsonify({"success": False, "message": "Failed to save users"}), 500

    return jsonify({"success": True})

# ----------------- Serve mysales.html from disk -----------------
@app.route('/mysales.html')
def mysales_page():
    """
    Serve the local mysales.html file (must be in same folder as app.py).
    Falls back to a small inline client if the file is missing so server stays usable.
    """
    local_file = os.path.join(os.getcwd(), 'mysales.html')
    if os.path.exists(local_file):
        # send the file directly from the app root
        try:
            return send_from_directory('.', 'mysales.html')
        except Exception:
            log.exception("send_from_directory failed for mysales.html")
    # Fallback: return the original simplified inline HTML (keeps server working if file missing)
    return Response("""
<!doctype html>
<html><head><meta charset="utf-8"/><title>MySales (fallback)</title></head><body>
<h3>MySales (Server mode) — fallback page</h3>
<p>If you want the enhanced UI, put <code>mysales.html</code> next to <code>app.py</code> and restart the server.</p>
</body></html>
""", mimetype='text/html')

@app.route('/api/whoami')
def api_whoami():
    if 'user_email' in session:
        users = load_users()
        email = session['user_email']
        user_doc = None
        for u in users:
            if isinstance(u, dict) and u.get('email') == email:
                user_doc = u
                break
        is_admin = bool(user_doc and user_doc.get('is_admin')) or (ADMIN_EMAIL and email==ADMIN_EMAIL) or bool(session.get('simple_admin'))
        return jsonify({"email": email, "isAdmin": is_admin})
    return jsonify({})

@app.route('/api/record_sale', methods=['POST'])
def api_record_sale():
    if 'user_email' not in session:
        return jsonify({"success": False, "message": "Authentication required."}), 401
    data = request.get_json() or {}
    product = data.get('product')
    price = data.get('price')
    currency = data.get('currency')
    if not (product and price and currency):
        return jsonify({"success": False, "message": "product, price, currency required"}), 400
    email = session['user_email']
    created_at = datetime.utcnow().isoformat()
    usd_value = None
    try:
        r = requests.get(f'https://api.exchangerate.host/latest?base=USD&symbols={currency},USD', timeout=8)
        jr = r.json()
        if jr and jr.get('rates') and jr['rates'].get(currency):
            rate = jr['rates'][currency]
            if rate:
                usd_value = float(price) / float(rate)
    except Exception:
        log.exception("failed to fetch exchange rate")

    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("""
            INSERT INTO sales (user_email, product, price, currency, usd_value, commission_rate, commission_amount, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (email, product, float(price), currency, usd_value, None, None, 'pending', created_at))
        conn.commit()
        sale_id = c.lastrowid
        conn.close()
        ok, resp = push_sales_db_to_github()
        if not ok:
            log.warning("sales.db push failed after record_sale: %s", resp)
        return jsonify({"success": True, "id": sale_id})
    except Exception:
        log.exception("Failed to record sale")
        return jsonify({"success": False, "message": "Server error"}), 500

@app.route('/api/get_sales')
def api_get_sales():
    if 'user_email' not in session and not session.get('simple_admin'):
        return jsonify({"success": False, "message": "Authentication required."}), 401
    email = session.get('user_email')
    is_admin = bool(session.get('simple_admin'))
    try:
        conn = get_db_connection()
        c = conn.cursor()
        if is_admin:
            c.execute("SELECT * FROM sales ORDER BY created_at DESC")
        else:
            c.execute("SELECT * FROM sales WHERE user_email = ? ORDER BY created_at DESC", (email,))
        rows = c.fetchall()
        conn.close()
        sales = []
        for r in rows:
            sales.append({
                "id": r["id"],
                "user_email": r["user_email"],
                "product": r["product"],
                "price": r["price"],
                "currency": r["currency"],
                "usd_value": r["usd_value"],
                "commission_rate": r["commission_rate"],
                "commission_amount": r["commission_amount"],
                "status": r["status"],
                "created_at": r["created_at"],
                "approved_at": r["approved_at"]
            })
        return jsonify({"success": True, "sales": sales})
    except Exception:
        log.exception("api_get_sales failed")
        return jsonify({"success": False, "message": "Server error"}), 500

def require_simple_admin_json_internal():
    if not session.get('simple_admin'):
        return jsonify({"success": False, "message": "Unauthorized"}), 403
    return None

@app.route('/api/approve_sale', methods=['POST'])
def api_approve_sale():
    admin_check = require_simple_admin_json_internal()
    if admin_check:
        return admin_check

    data = request.get_json() or {}
    sale_id = data.get('id')
    if not sale_id:
        return jsonify({"success": False, "message": "id required"}), 400

    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT * FROM sales WHERE id = ?", (sale_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "message": "sale not found"}), 404

        if row["status"] != 'pending':
            conn.close()
            return jsonify({"success": False, "message": "sale not pending"}), 400

        user_email = row["user_email"]

        seven_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
        c.execute("SELECT COUNT(*) as cnt FROM sales WHERE user_email = ? AND status = 'approved' AND created_at >= ?", (user_email, seven_ago))
        cnt_row = c.fetchone()
        approved_count = cnt_row["cnt"] if cnt_row else 0

        prospective = (approved_count or 0) + 1

        try:
            rate = BONUS_COMMISSION if prospective >= BONUS_THRESHOLD else DEFAULT_COMMISSION
        except NameError:
            rate = 0.10

        base_value = None
        try:
            base_value = float(row["usd_value"]) if row["usd_value"] is not None else float(row["price"])
        except Exception:
            try:
                base_value = float(row["price"])
            except Exception:
                base_value = 0.0

        commission_amount = round(base_value * float(rate), 2)

        approved_at = datetime.utcnow().isoformat()
        c.execute(
            "UPDATE sales SET status = 'approved', commission_rate = ?, commission_amount = ?, approved_at = ? WHERE id = ?",
            (rate, commission_amount, approved_at, sale_id)
        )
        conn.commit()
        conn.close()

        ok, resp = push_sales_db_to_github()
        if not ok:
            log.warning("sales.db push failed after approve: %s", resp)

        return jsonify({
            "success": True,
            "commission_rate": rate,
            "commission_amount": commission_amount,
            "approved_at": approved_at
        }), 200

    except Exception as exc:
        log.exception("approve_sale failed")
        return jsonify({"success": False, "message": "Server error", "error": str(exc)}), 500

@app.route('/api/reject_sale', methods=['POST'])
def api_reject_sale():
    admin_check = require_simple_admin_json_internal()
    if admin_check:
        return admin_check
    data = request.get_json() or {}
    sale_id = data.get('id')
    if not sale_id:
        return jsonify({"success": False, "message": "id required"}), 400
    try:
        conn = get_db_connection()
        c = conn.cursor()
        c.execute("SELECT * FROM sales WHERE id = ?", (sale_id,))
        row = c.fetchone()
        if not row:
            conn.close()
            return jsonify({"success": False, "message": "sale not found"}), 404
        if row["status"] != 'pending':
            conn.close()
            return jsonify({"success": False, "message": "sale not pending"}), 400
        c.execute("UPDATE sales SET status = 'rejected', approved_at = ? WHERE id = ?", (datetime.utcnow().isoformat(), sale_id))
        conn.commit()
        conn.close()
        ok, resp = push_sales_db_to_github()
        if not ok:
            log.warning("sales.db push failed after reject: %s", resp)
        return jsonify({"success": True})
    except Exception:
        log.exception("reject_sale failed")
        return jsonify({"success": False, "message": "Server error"}), 500

# ---------------- End of mysales additions -----------------

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
