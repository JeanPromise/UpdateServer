from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import time
import tempfile
import urllib.error
import urllib.request
import zipfile
from contextlib import contextmanager
try:
    import fcntl
except ImportError:  # Windows development fallback; Render runs on Linux.
    fcntl = None
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path
from urllib.parse import quote, urlparse

from flask import (
    Flask, abort, flash, g, jsonify, make_response, redirect, render_template,
    request, send_file, send_from_directory, session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.middleware.proxy_fix import ProxyFix

BASE_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = BASE_DIR / "instance"
INSTANCE_DIR.mkdir(exist_ok=True)
# Render persistent disks can be mounted at /var/data. If no disk is mounted,
# fall back to the app's local instance folder. See README for persistence notes.
PERSISTENT_DIR = Path("/var/data")
if PERSISTENT_DIR.exists() and os.access(PERSISTENT_DIR, os.W_OK):
    DB_PATH = Path(os.environ.get("DATABASE_PATH", str(PERSISTENT_DIR / "barcodehub.sqlite3")))
else:
    DB_PATH = Path(os.environ.get("DATABASE_PATH", str(INSTANCE_DIR / "barcodehub.sqlite3")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)
DB_LOCK_PATH = Path(str(DB_PATH) + ".lock")
PRODUCT_IMAGE_DIR = DB_PATH.parent / "product-images"
PRODUCT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
ALLOWED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
MAX_PRODUCT_IMAGE_BYTES = 8 * 1024 * 1024

app = Flask(__name__, instance_path=str(INSTANCE_DIR))
if os.environ.get("RENDER_EXTERNAL_URL"):
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
# No third Render secret is required. Generate one local signing key once so all
# Gunicorn workers share it. It may be regenerated after a redeploy on ephemeral
# storage, in which case browser sessions simply need to sign in again.
def load_session_secret() -> str:
    key_path = INSTANCE_DIR / ".session-key"
    try:
        if key_path.exists():
            existing = key_path.read_text(encoding="utf-8").strip()
            if len(existing) >= 64:
                return existing
        generated = secrets.token_hex(48)
        try:
            fd = os.open(str(key_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(generated)
            return generated
        except FileExistsError:
            # Another Gunicorn worker may be creating the shared file. Wait for
            # its atomic write to finish instead of choosing a different key.
            for _ in range(100):
                try:
                    existing = key_path.read_text(encoding="utf-8").strip()
                    if len(existing) >= 64:
                        return existing
                except OSError:
                    pass
                time.sleep(0.01)
        except OSError:
            pass
    except OSError:
        pass
    # Only used if local storage is unavailable. Derive a stable fallback from
    # the configured admin credentials when available; never use a known key.
    username = os.environ.get("ADMIN_USERNAME", "")
    password = os.environ.get("ADMIN_PASSWORD", "")
    if username and password:
        salt = ("kenya-barcode-hub-session-v1:" + username).encode()
        return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 120_000).hex()
    return secrets.token_hex(48)


app.secret_key = load_session_secret()
app.config.update(
    MAX_CONTENT_LENGTH=128 * 1024 * 1024,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=bool(os.environ.get("RENDER_EXTERNAL_URL")),
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
)

DEFAULT_SETTINGS = {
    "site_name": "Kenya Barcode Hub",
    "tagline": "Product data. Ready to use.",
    "description": "A practical barcode and product catalogue for Kenyan shops, POS builders, and developers.",
    "hero_title": "Product barcodes.",
    "hero_highlight": "Ready for your system.",
    "hero_subtitle": "Search, download and connect to product records made for Kenyan retail.",
    "logo_url": "",
    "primary_color": "#2677e8",
    "support_email": "",
    "support_phone": "",
    "mpesa_till": "",
    "mpesa_paybill": "",
    "mpesa_account": "",
    "mpesa_phone": "",
    "payment_instructions": "Pay using the M-Pesa details shown below, then submit the transaction code. Access is enabled after an administrator verifies the payment.",
    "homepage_notice": "Building a more useful product catalogue for Kenya.",
    "catalogue_intro": "Search product records or connect your app through the API.",
    "api_enabled": "1",
    "public_search_limit": "5",
}

DEFAULT_PLANS = [
    ("starter", "Starter API", 100, 1000, 30, "For testing, small shops, and personal projects", 1),
    ("developer", "Developer API", 300, 10000, 30, "For active POS and inventory applications", 1),
    ("business", "Business API", 1000, 100000, 30, "For higher-volume applications", 1),
]

# These image-inclusive plans are intentionally unpublished and unpriced on a
# fresh install. The owner sets the actual image-data prices in Admin → Plans.
DEFAULT_IMAGE_PLANS = [
    ("starter-images", "Starter API + Images", 0, 1000, 30,
     "Starter API access including product image URLs. Set a price before publishing.", 0, 1),
    ("developer-images", "Developer API + Images", 0, 10000, 30,
     "Developer API access including product image URLs. Set a price before publishing.", 0, 1),
    ("business-images", "Business API + Images", 0, 100000, 30,
     "Higher-volume API access including product image URLs. Set a price before publishing.", 0, 1),
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def log_system_event(source: str, event_type: str, message: str,
                     status_code: int | None = None, user_id: int | None = None,
                     path: str = "", severity: str | None = None) -> None:
    """Store a short, safe diagnostic event without secrets or stack traces."""
    if severity is None:
        severity = "error" if status_code and status_code >= 500 else (
            "warning" if status_code and status_code >= 400 else "info")
    try:
        with connect_db() as db:
            db.execute("""INSERT INTO system_events
                          (event_type,severity,source,message,status_code,user_id,path,created_at)
                          VALUES(?,?,?,?,?,?,?,?)""",
                       (str(event_type)[:80], str(severity)[:20], str(source)[:50],
                        str(message)[:500], status_code, user_id, str(path)[:300], now_iso()))
    except Exception:
        # Diagnostics must never prevent the original request from completing.
        app.logger.exception("Could not save a system diagnostic event")


def mask_email(value: str) -> str:
    value = (value or "").strip()
    if "@" not in value:
        return "unmatched email"
    local, domain = value.split("@", 1)
    return f"{local[:1]}***@{domain[:120]}"


def valid_image_url(value: str) -> bool:
    value = (value or "").strip()
    if not value:
        return True
    if value.startswith("/media/products/"):
        tail = value.removeprefix("/media/products/")
        return bool(tail) and "/" not in tail and ".." not in tail and Path(tail).suffix.lower() in ALLOWED_IMAGE_EXTENSIONS
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc) and len(value) <= 2000


def save_product_image(upload, barcode: str) -> str:
    """Validate and save a single user-provided product image to persistent media."""
    if not upload or not upload.filename:
        return ""
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in ALLOWED_IMAGE_EXTENSIONS:
        raise ValueError("Use a JPG, PNG, WEBP or GIF image.")
    content = upload.stream.read(MAX_PRODUCT_IMAGE_BYTES + 1)
    if not content:
        raise ValueError("The selected image is empty.")
    if len(content) > MAX_PRODUCT_IMAGE_BYTES:
        raise ValueError("Each product image must be 8 MB or smaller.")
    actual_ext = None
    if content.startswith(b"\xff\xd8\xff"):
        actual_ext = ".jpg"
    elif content.startswith(b"\x89PNG\r\n\x1a\n"):
        actual_ext = ".png"
    elif content.startswith((b"GIF87a", b"GIF89a")):
        actual_ext = ".gif"
    elif len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        actual_ext = ".webp"
    if actual_ext is None:
        raise ValueError("The uploaded file does not look like a supported image.")
    token = hashlib.sha256((barcode + secrets.token_hex(16)).encode()).hexdigest()[:20]
    filename = token + actual_ext
    PRODUCT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    (PRODUCT_IMAGE_DIR / filename).write_bytes(content)
    return "/media/products/" + filename


class ClosingConnection(sqlite3.Connection):
    def close(self):
        lock_handle = getattr(self, "_hub_lock_handle", None)
        try:
            super().close()
        finally:
            if lock_handle is not None:
                try:
                    if fcntl is not None:
                        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
                finally:
                    lock_handle.close()
                    self._hub_lock_handle = None

    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()


@contextmanager
def database_file_lock(exclusive=False):
    """Coordinate restore with every application DB connection across workers."""
    handle = open(DB_LOCK_PATH, "a+b")
    try:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        yield handle
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def connect_db() -> sqlite3.Connection:
    lock_handle = open(DB_LOCK_PATH, "a+b")
    conn = None
    try:
        if fcntl is not None:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_SH)
        conn = sqlite3.connect(DB_PATH, timeout=30, factory=ClosingConnection)
        conn._hub_lock_handle = lock_handle
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn
    except Exception:
        if conn is not None:
            conn.close()  # also releases its shared file lock
        else:
            try:
                if fcntl is not None:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            finally:
                lock_handle.close()
        raise


def init_db() -> None:
    with connect_db() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            password_changed_at TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            barcode TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            brand TEXT DEFAULT '',
            pack_size TEXT DEFAULT '',
            category TEXT DEFAULT '',
            price_kes REAL,
            verified INTEGER NOT NULL DEFAULT 0,
            source TEXT DEFAULT '',
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_products_name ON products(name);
        CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            summary TEXT DEFAULT '',
            url TEXT DEFAULT '',
            kind TEXT NOT NULL DEFAULT 'link',
            premium INTEGER NOT NULL DEFAULT 0,
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            slug TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            price_kes INTEGER NOT NULL,
            lookup_limit INTEGER NOT NULL,
            duration_days INTEGER NOT NULL DEFAULT 30,
            description TEXT DEFAULT '',
            active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            plan_id INTEGER NOT NULL REFERENCES plans(id),
            amount_kes INTEGER NOT NULL,
            payer_name TEXT NOT NULL,
            payer_phone TEXT NOT NULL,
            transaction_code TEXT NOT NULL COLLATE NOCASE,
            status TEXT NOT NULL DEFAULT 'pending',
            admin_note TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            reviewed_at TEXT DEFAULT '',
            UNIQUE(transaction_code)
        );
        CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status, created_at);
        CREATE TABLE IF NOT EXISTS subscriptions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            plan_id INTEGER NOT NULL REFERENCES plans(id),
            starts_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            order_id INTEGER NOT NULL REFERENCES orders(id),
            UNIQUE(order_id)
        );
        CREATE INDEX IF NOT EXISTS idx_subscriptions_user ON subscriptions(user_id, expires_at);
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            label TEXT NOT NULL DEFAULT 'My application',
            prefix TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            revoked INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS api_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            barcode TEXT DEFAULT '',
            status_code INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_api_logs_user_date ON api_logs(user_id, created_at);
        CREATE TABLE IF NOT EXISTS visitor_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visitor_id TEXT NOT NULL,
            path TEXT NOT NULL,
            method TEXT NOT NULL,
            ip_masked TEXT DEFAULT '',
            referrer TEXT DEFAULT '',
            user_agent TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL
        );
        CREATE INDEX IF NOT EXISTS idx_visitors_date ON visitor_events(created_at);
        CREATE INDEX IF NOT EXISTS idx_visitors_visitor ON visitor_events(visitor_id);
        CREATE TABLE IF NOT EXISTS system_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT NOT NULL,
            severity TEXT NOT NULL DEFAULT 'info',
            source TEXT NOT NULL,
            message TEXT NOT NULL,
            status_code INTEGER,
            user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
            path TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_system_events_date ON system_events(created_at);
        CREATE INDEX IF NOT EXISTS idx_system_events_source_status ON system_events(source,status_code);
        """)
        # Forward-only schema migrations keep existing Render SQLite data intact.
        product_columns = {row[1] for row in db.execute("PRAGMA table_info(products)")}
        if "image_url" not in product_columns:
            db.execute("ALTER TABLE products ADD COLUMN image_url TEXT DEFAULT ''")
        if "image_source" not in product_columns:
            db.execute("ALTER TABLE products ADD COLUMN image_source TEXT DEFAULT ''")
        if "image_checked_at" not in product_columns:
            db.execute("ALTER TABLE products ADD COLUMN image_checked_at TEXT DEFAULT ''")
        plan_columns = {row[1] for row in db.execute("PRAGMA table_info(plans)")}
        if "image_access" not in plan_columns:
            db.execute("ALTER TABLE plans ADD COLUMN image_access INTEGER NOT NULL DEFAULT 0")
        user_columns = {row[1] for row in db.execute("PRAGMA table_info(users)")}
        if "password_changed_at" not in user_columns:
            db.execute("ALTER TABLE users ADD COLUMN password_changed_at TEXT DEFAULT ''")
        db.execute("CREATE INDEX IF NOT EXISTS idx_products_image_url ON products(image_url)")
        visitor_columns = {row[1] for row in db.execute("PRAGMA table_info(visitor_events)")}
        if "user_id" not in visitor_columns:
            db.execute("ALTER TABLE visitor_events ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
        db.execute("CREATE INDEX IF NOT EXISTS idx_visitors_user ON visitor_events(user_id)")
        for key, value in DEFAULT_SETTINGS.items():
            db.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (key, value))
        for slug, name, price, limit, days, description, active in DEFAULT_PLANS:
            db.execute("""INSERT OR IGNORE INTO plans(slug,name,price_kes,lookup_limit,duration_days,description,active)
                          VALUES (?,?,?,?,?,?,?)""", (slug, name, price, limit, days, description, active))
        for slug, name, price, limit, days, description, active, image_access in DEFAULT_IMAGE_PLANS:
            db.execute("""INSERT OR IGNORE INTO plans
                          (slug,name,price_kes,lookup_limit,duration_days,description,active,image_access)
                          VALUES (?,?,?,?,?,?,?,?)""",
                       (slug, name, price, limit, days, description, active, image_access))
        seed_marker = db.execute("SELECT value FROM settings WHERE key='seed_catalogue_loaded'").fetchone()
        if not seed_marker:
            product_count = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
            seed_path = BASE_DIR / "data-seed" / "kenya-barcode-hub-seed.csv"
            if product_count == 0 and seed_path.exists():
                with seed_path.open("r", encoding="utf-8-sig", newline="") as handle:
                    for raw in csv.DictReader(handle):
                        barcode = re.sub(r"[\s-]+", "", (raw.get("barcode") or "").strip())
                        name = (raw.get("name") or "").strip()[:200]
                        if not barcode or not name:
                            continue
                        price_text = (raw.get("price_kes") or "").strip().replace(",", "")
                        price_match = re.search(r"-?\d+(?:\.\d+)?", price_text)
                        try:
                            price = float(price_match.group(0)) if price_match else None
                            if price is not None and price < 0:
                                price = None
                        except ValueError:
                            price = None
                        verified = 1 if str(raw.get("verified", "0")).strip().lower() in ("1", "true", "yes") else 0
                        db.execute("""INSERT OR IGNORE INTO products(barcode,name,brand,pack_size,category,price_kes,verified,source,updated_at)
                                      VALUES(?,?,?,?,?,?,?,?,?)""",
                                   (barcode[:50], name, (raw.get("brand") or "")[:120], (raw.get("pack_size") or "")[:100],
                                    (raw.get("category") or "")[:100], price, verified, (raw.get("source") or "")[:600], now_iso()))
            db.execute("INSERT INTO settings(key,value) VALUES('seed_catalogue_loaded','1') ON CONFLICT(key) DO UPDATE SET value='1'")


init_db()


def get_settings(db=None) -> dict[str, str]:
    own = db is None
    db = db or connect_db()
    values = DEFAULT_SETTINGS.copy()
    values.update({r["key"]: r["value"] for r in db.execute("SELECT key,value FROM settings")})
    if own:
        db.close()
    return values


def admin_credentials_ok(username: str, password: str) -> bool:
    expected_user = os.environ.get("ADMIN_USERNAME", "").strip()
    expected_password = os.environ.get("ADMIN_PASSWORD", "")
    if not expected_user or not expected_password:
        return False
    return hmac.compare_digest(username.strip(), expected_user) and hmac.compare_digest(password, expected_password)


def is_admin() -> bool:
    if session.get("is_admin") is not True:
        return False
    configured_name = os.environ.get("ADMIN_USERNAME", "").strip()
    configured_password = os.environ.get("ADMIN_PASSWORD", "")
    if not configured_name or not configured_password:
        return False
    expected_fingerprint = hashlib.sha256(configured_password.encode()).hexdigest()
    return hmac.compare_digest(str(session.get("admin_user", "")), configured_name) and hmac.compare_digest(
        str(session.get("admin_password_fingerprint", "")), expected_fingerprint
    )


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    with connect_db() as db:
        row = db.execute("SELECT id,name,email,created_at,password_changed_at FROM users WHERE id=?", (user_id,)).fetchone()
        if not row:
            session.pop("user_id", None)
            session.pop("user_password_version", None)
            return None
        version = row["password_changed_at"] or row["created_at"]
        session_version = session.get("user_password_version")
        if session_version and not hmac.compare_digest(str(session_version), str(version)):
            session.pop("user_id", None)
            session.pop("user_password_version", None)
            return None
        if not session_version:
            session["user_password_version"] = version
        result = dict(row)
        result.pop("password_changed_at", None)
        return result


def active_subscription(user_id: int, db=None):
    own = db is None
    db = db or connect_db()
    now = now_iso()
    row = db.execute("""SELECT s.*, p.name AS plan_name, p.lookup_limit, p.price_kes,
                              COALESCE(p.image_access,0) AS image_access
                       FROM subscriptions s JOIN plans p ON p.id=s.plan_id
                       WHERE s.user_id=? AND s.starts_at<=? AND s.expires_at>?
                       ORDER BY s.expires_at DESC LIMIT 1""", (user_id, now, now)).fetchone()
    result = dict(row) if row else None
    if own:
        db.close()
    return result


def monthly_usage(user_id: int, sub: dict, db=None) -> int:
    own = db is None
    db = db or connect_db()
    row = db.execute("SELECT COUNT(*) AS n FROM api_logs WHERE user_id=? AND created_at>=? AND created_at<? AND status_code != 429",
                     (user_id, sub["starts_at"], sub["expires_at"])).fetchone()
    count = int(row["n"] if row else 0)
    if own:
        db.close()
    return count


def csrf_token() -> str:
    token = session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["csrf_token"] = token
    return token


app.jinja_env.globals["csrf_token"] = csrf_token
app.jinja_env.globals["get_settings"] = get_settings


def mask_ip(addr: str) -> str:
    if not addr:
        return ""
    # Keep rough network context without recording a full visitor IP address.
    if "." in addr:
        parts = addr.split(".")
        if len(parts) == 4:
            return ".".join(parts[:3]) + ".x"
    if ":" in addr:
        return ":".join(addr.split(":")[:4]) + ":…"
    return "masked"


def is_safe_external_url(value: str) -> bool:
    if not value:
        return True
    parsed = urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def log_page_view() -> None:
    """Record eligible browser page requests, excluding infrastructure and obvious bots.

    A web server cannot prove that a request came from a unique physical device:
    browser/device fingerprinting is unreliable and privacy-invasive. visitor_id is
    a first-party session identifier, so analytics below report browser sessions,
    not guaranteed unique people or physical devices.
    """
    if request.endpoint in ("static", "health", "service_worker", "manifest", "favicon") or request.path.startswith("/media/"):
        return
    if request.path.startswith(("/promise212324", "/api/", "/downloads/", "/admin", "/login", "/logout")):
        return
    if request.method != "GET":
        return
    # Exclude common crawlers, health checks, command-line clients and monitoring agents.
    ua = (request.user_agent.string or "").lower()
    bot_markers = (
        "bot", "crawler", "spider", "slurp", "headless", "lighthouse",
        "pagespeed", "uptime", "monitor", "probe", "curl/", "wget/",
        "python-requests", "python-urllib", "httpx/", "aiohttp", "scrapy",
        "facebookexternalhit", "preview"
    )
    if not ua or any(marker in ua for marker in bot_markers):
        return
    # Do not count requests that do not look like a normal browser navigation.
    if not any(marker in ua for marker in ("mozilla/", "chrome/", "safari/", "firefox/", "edg/")):
        return
    visitor_id = session.get("visitor_id")
    if not visitor_id:
        visitor_id = secrets.token_urlsafe(14)
        session["visitor_id"] = visitor_id
    referrer = request.referrer or ""
    if referrer:
        parsed = urlparse(referrer)
        referrer = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"[:400]
    try:
        with connect_db() as db:
            db.execute("""INSERT INTO visitor_events(visitor_id,path,method,ip_masked,referrer,user_agent,created_at)
                          VALUES(?,?,?,?,?,?,?)""",
                       (visitor_id, request.path[:300], request.method, mask_ip(request.remote_addr or ""),
                        referrer, request.user_agent.string[:300], now_iso()))
    except sqlite3.Error:
        app.logger.exception("Could not record visitor event")


@app.before_request
def security_and_analytics():
    # Validate CSRF on all normal browser POSTs.
    if request.method == "POST" and not request.path.startswith("/api/"):
        submitted = request.form.get("csrf_token", "")
        expected = session.get("csrf_token", "")
        if not expected or not hmac.compare_digest(submitted, expected):
            abort(400, description="Your form expired. Refresh the page and try again.")
    log_page_view()


@app.after_request
def security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if request.path.startswith("/promise212324") or request.path.startswith("/account") or request.path.startswith("/checkout") or request.path.startswith("/login") or request.path.startswith("/register") or request.path.startswith("/api/") or request.path.startswith("/downloads/"):
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
    # Only explicitly public, query-free anonymous pages may be cached offline.
    public_offline_pages = {"/", "/offline", "/pricing", "/docs", "/catalogue", "/privacy"}
    if (request.method == "GET" and request.path in public_offline_pages and not request.query_string
            and not session.get("user_id") and not is_admin() and response.status_code == 200):
        response.headers["X-Offline-Cacheable"] = "public"
    if request.path.startswith("/api/v1/barcode/"):
        code = response.status_code
        status_messages = {
            200: "Barcode lookup succeeded",
            401: "API key missing, invalid or revoked",
            402: "API request blocked: no active subscription",
            404: "Barcode not found in catalogue",
            429: "API lookup limit reached",
            503: "API disabled or temporarily unavailable",
        }
        message = status_messages.get(code, "API request returned an unexpected status" if code >= 400 else "API request completed")
        log_system_event("api", "barcode_lookup", message, code,
                         getattr(g, "api_user_id", None), request.path,
                         "error" if code >= 500 else ("warning" if code >= 400 else "info"))
    elif response.status_code >= 500:
        log_system_event("system", "server_error", "Server returned an internal error response",
                         response.status_code, getattr(g, "api_user_id", None), request.path, "error")
    return response


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id") or not current_user():
            flash("Sign in to continue.", "info")
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_admin():
            return redirect(url_for("admin_login"))
        return view(*args, **kwargs)
    return wrapped


def normalize_barcode(value: str) -> str:
    return "".join(ch for ch in str(value or "").strip() if ch not in " \t\r\n-")


def product_from_row(row):
    d = dict(row)
    d["verified"] = bool(d.get("verified"))
    return d


def query_products(term: str, limit: int = 20):
    term = (term or "").strip()[:100]
    with connect_db() as db:
        if not term:
            rows = db.execute("SELECT * FROM products ORDER BY name COLLATE NOCASE LIMIT ?", (limit,)).fetchall()
        else:
            like = f"%{term}%"
            rows = db.execute("""SELECT * FROM products WHERE barcode=? OR name LIKE ? OR brand LIKE ? OR category LIKE ?
                              ORDER BY CASE WHEN barcode=? THEN 0 ELSE 1 END, name COLLATE NOCASE LIMIT ?""",
                              (normalize_barcode(term), like, like, like, normalize_barcode(term), limit)).fetchall()
    return [product_from_row(r) for r in rows]


def active_plans():
    with connect_db() as db:
        return [dict(r) for r in db.execute("SELECT * FROM plans WHERE active=1 ORDER BY price_kes ASC")]


@app.context_processor
def inject_context():
    with connect_db() as db:
        pending = db.execute("SELECT COUNT(*) FROM orders WHERE status='pending'").fetchone()[0]
    return {"site_settings": get_settings(), "signed_in_user": current_user(), "is_admin_session": is_admin(),
            "current_year": datetime.now(timezone.utc).year, "admin_pending_count": pending}


@app.route("/")
def index():
    q = request.args.get("q", "")[:100]
    # Public lookup is deliberately limited; the paid API/full catalogue supplies bulk access.
    settings = get_settings()
    try:
        public_limit = max(1, min(10, int(settings.get("public_search_limit", "5"))))
    except ValueError:
        public_limit = 5
    results = query_products(q, public_limit) if q else []
    with connect_db() as db:
        product_count = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        verified_count = db.execute("SELECT COUNT(*) FROM products WHERE verified=1").fetchone()[0]
    with connect_db() as db:
        posts = [dict(r) for r in db.execute("SELECT * FROM posts WHERE active=1 ORDER BY id DESC LIMIT 6")]
    user = current_user()
    has_access = bool(user and active_subscription(user["id"]))
    return render_template("index.html", q=q, results=results, product_count=product_count,
                           verified_count=verified_count, plans=active_plans(), search_limit=public_limit,
                           posts=posts, user_has_access=has_access)


@app.route("/catalogue")
def catalogue():
    q = request.args.get("q", "")[:100]
    results = query_products(q, 10) if q else query_products("", 10)
    return render_template("catalogue.html", q=q, results=results)


@app.route("/resources/<int:post_id>")
def resource_detail(post_id):
    with connect_db() as db:
        row = db.execute("SELECT * FROM posts WHERE id=? AND active=1", (post_id,)).fetchone()
    if not row:
        abort(404)
    post = dict(row)
    user = current_user()
    has_access = bool(user and active_subscription(user["id"]))
    if post["premium"] and not has_access:
        flash("This resource is available to active plan holders.", "info")
        return redirect(url_for("pricing"))
    if post["url"] and is_safe_external_url(post["url"]):
        return redirect(post["url"])
    return render_template("resource.html", post=post)


@app.route("/pricing")
def pricing():
    return render_template("pricing.html", plans=active_plans())


@app.route("/docs")
def docs():
    return render_template("docs.html")


@app.route("/privacy")
def privacy():
    return render_template("privacy.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()[:100]
        email = request.form.get("email", "").strip().lower()[:254]
        password = request.form.get("password", "")
        if len(name) < 2 or "@" not in email or len(password) < 10:
            flash("Enter your name, a valid email, and a password with at least 10 characters.", "error")
            return render_template("register.html")
        try:
            with connect_db() as db:
                created = now_iso()
                cursor = db.execute("INSERT INTO users(name,email,password_hash,created_at,password_changed_at) VALUES(?,?,?,?,?)",
                                    (name, email, generate_password_hash(password), created, created))
                user_id = cursor.lastrowid
        except sqlite3.IntegrityError:
            flash("An account with that email already exists.", "error")
            return render_template("register.html")
        session.clear()
        session["user_id"] = user_id
        session["user_password_version"] = created
        csrf_token()
        flash("Your account is ready.", "success")
        target = request.args.get("next", "")
        if not target.startswith("/") or target.startswith("//") or target.startswith("/promise212324"):
            target = url_for("account")
        return redirect(target)
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()[:254]
        password = request.form.get("password", "")
        with connect_db() as db:
            user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if not user or not check_password_hash(user["password_hash"], password):
            log_system_event("authentication", "user_login_failed",
                             "Customer sign-in rejected for " + mask_email(email), 401,
                             user["id"] if user else None, request.path, "warning")
            time.sleep(0.25)
            flash("Email or password is incorrect.", "error")
            return render_template("login.html")
        session.clear()
        session["user_id"] = user["id"]
        session["user_password_version"] = user["password_changed_at"] or user["created_at"]
        session["visitor_id"] = secrets.token_urlsafe(14)
        csrf_token()
        log_system_event("authentication", "user_login_succeeded", "Customer signed in successfully",
                         200, user["id"], request.path, "info")
        target = request.args.get("next", "")
        if not target.startswith("/") or target.startswith("//") or target.startswith("/promise212324"):
            target = url_for("account")
        return redirect(target)
    return render_template("login.html")


@app.post("/logout")
@login_required
def logout():
    session.clear()
    flash("You have signed out.", "info")
    return redirect(url_for("index"))


@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    user = current_user()
    new_api_key = None
    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "create_key":
            label = request.form.get("label", "My application").strip()[:80] or "My application"
            raw = "kbh_" + secrets.token_urlsafe(32)
            digest = hashlib.sha256(raw.encode()).hexdigest()
            with connect_db() as db:
                db.execute("INSERT INTO api_keys(user_id,label,prefix,token_hash,created_at) VALUES(?,?,?,?,?)",
                           (user["id"], label, raw[:12], digest, now_iso()))
            new_api_key = raw
            flash("API key created. Copy it now; the full key is only shown once.", "success")
        elif action == "revoke_key":
            try:
                key_id = int(request.form.get("key_id", "0"))
            except ValueError:
                key_id = 0
            with connect_db() as db:
                db.execute("UPDATE api_keys SET revoked=1 WHERE id=? AND user_id=?", (key_id, user["id"]))
            flash("API key revoked.", "success")
        elif action == "change_password":
            current_password = request.form.get("current_password", "")
            new_password = request.form.get("new_password", "")
            confirm_password = request.form.get("confirm_password", "")
            password_changed = False
            with connect_db() as db:
                auth_user = db.execute("SELECT password_hash FROM users WHERE id=?", (user["id"],)).fetchone()
                if not auth_user or not check_password_hash(auth_user["password_hash"], current_password):
                    flash("Your current password was not correct.", "error")
                elif len(new_password) < 10:
                    flash("Choose a new password with at least 10 characters.", "error")
                elif new_password != confirm_password:
                    flash("The new passwords do not match.", "error")
                elif new_password == current_password:
                    flash("Choose a different new password.", "error")
                else:
                    changed_at = datetime.now(timezone.utc).isoformat()
                    db.execute("UPDATE users SET password_hash=?,password_changed_at=? WHERE id=?",
                               (generate_password_hash(new_password), changed_at, user["id"]))
                    session["user_password_version"] = changed_at
                    password_changed = True
                    flash("Your password has been changed.", "success")
            if password_changed:
                log_system_event("authentication", "user_password_changed",
                                 "Customer changed their password", 200, user["id"], request.path, "info")
        return render_template("account.html", user=user, subscription=active_subscription(user["id"]),
                               usage=monthly_usage(user["id"], active_subscription(user["id"])) if active_subscription(user["id"]) else 0,
                               keys=get_user_keys(user["id"]), orders=get_user_orders(user["id"]), new_api_key=new_api_key)
    sub = active_subscription(user["id"])
    return render_template("account.html", user=user, subscription=sub,
                           usage=monthly_usage(user["id"], sub) if sub else 0,
                           keys=get_user_keys(user["id"]), orders=get_user_orders(user["id"]), new_api_key=None)


def get_user_keys(user_id: int):
    with connect_db() as db:
        return [dict(r) for r in db.execute("SELECT * FROM api_keys WHERE user_id=? ORDER BY id DESC", (user_id,))]


def get_user_orders(user_id: int):
    with connect_db() as db:
        return [dict(r) for r in db.execute("""SELECT o.*,p.name AS plan_name FROM orders o JOIN plans p ON p.id=o.plan_id
                                                WHERE o.user_id=? ORDER BY o.id DESC LIMIT 20""", (user_id,))]


@app.route("/checkout/<slug>", methods=["GET", "POST"])
@login_required
def checkout(slug):
    user = current_user()
    with connect_db() as db:
        plan_row = db.execute("SELECT * FROM plans WHERE slug=? AND active=1", (slug,)).fetchone()
    if not plan_row:
        abort(404)
    plan = dict(plan_row)
    settings = get_settings()
    if request.method == "POST":
        payer_name = request.form.get("payer_name", "").strip()[:100]
        payer_phone = request.form.get("payer_phone", "").strip()[:40]
        transaction_code = request.form.get("transaction_code", "").strip().upper()[:80]
        amount_text = request.form.get("amount_kes", "").strip()
        try:
            amount = int(amount_text)
        except ValueError:
            amount = 0
        if not payer_name or not payer_phone or not transaction_code:
            flash("Complete the payer name, phone number, and M-Pesa transaction code.", "error")
            return render_template("checkout.html", plan=plan, settings=settings)
        if amount != int(plan["price_kes"]):
            flash(f"The submitted amount must be KSh {plan['price_kes']} for this plan.", "error")
            return render_template("checkout.html", plan=plan, settings=settings)
        if len(transaction_code) < 5:
            flash("Enter the transaction code shown on the M-Pesa receipt.", "error")
            return render_template("checkout.html", plan=plan, settings=settings)
        try:
            with connect_db() as db:
                cur = db.execute("""INSERT INTO orders(user_id,plan_id,amount_kes,payer_name,payer_phone,transaction_code,status,created_at)
                                    VALUES(?,?,?,?,?,?, 'pending', ?)""",
                                 (user["id"], plan["id"], amount, payer_name, payer_phone, transaction_code, now_iso()))
                order_id = cur.lastrowid
        except sqlite3.IntegrityError:
            flash("That transaction code has already been submitted. Check the code or contact support if you believe this is a mistake.", "error")
            return render_template("checkout.html", plan=plan, settings=settings)
        flash("Payment details submitted. Your access will unlock after an administrator verifies the transaction.", "success")
        return redirect(url_for("account"))
    return render_template("checkout.html", plan=plan, settings=settings)


@app.route("/downloads/sample.csv")
def sample_download():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["barcode", "name", "brand", "pack_size", "category", "price_kes", "verified", "source", "image_url"])
    # A header-only file avoids presenting made-up barcode data as real products.
    data = io.BytesIO(output.getvalue().encode("utf-8-sig"))
    return send_file(data, mimetype="text/csv", as_attachment=True, download_name="kenya-barcode-hub-sample-template.csv")


@app.route("/downloads/catalogue.csv")
@login_required
def catalogue_download():
    user = current_user()
    sub = active_subscription(user["id"])
    if not sub:
        flash("An active plan is needed to download the full catalogue.", "info")
        return redirect(url_for("pricing"))
    image_access = bool(sub.get("image_access"))
    with connect_db() as db:
        if image_access:
            rows = db.execute("SELECT barcode,name,brand,pack_size,category,price_kes,verified,source,updated_at,image_url FROM products ORDER BY name COLLATE NOCASE").fetchall()
        else:
            rows = db.execute("SELECT barcode,name,brand,pack_size,category,price_kes,verified,source,updated_at FROM products ORDER BY name COLLATE NOCASE").fetchall()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["barcode", "name", "brand", "pack_size", "category", "price_kes", "verified", "source", "updated_at"] + (["image_url"] if image_access else []))
    for row in rows:
        values = list(row)
        if image_access and values[-1] and str(values[-1]).startswith("/"):
            values[-1] = request.url_root.rstrip("/") + str(values[-1])
        writer.writerow(values)
    response = make_response("\ufeff" + output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = 'attachment; filename="kenya-barcode-hub-catalogue.csv"'
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route("/api/v1/barcode/<barcode>", methods=["GET"])
def api_barcode(barcode):
    settings = get_settings()
    if settings.get("api_enabled", "1") != "1":
        return jsonify({"error": "api_disabled", "message": "The API is temporarily unavailable."}), 503
    raw_key = request.headers.get("X-API-Key", "")
    if not raw_key and request.headers.get("Authorization", "").lower().startswith("bearer "):
        raw_key = request.headers.get("Authorization", "")[7:].strip()
    if not raw_key:
        return jsonify({"error": "api_key_required", "message": "Send your API key in the X-API-Key header."}), 401
    digest = hashlib.sha256(raw_key.encode()).hexdigest()
    with connect_db() as db:
        key = db.execute("SELECT id,user_id FROM api_keys WHERE token_hash=? AND revoked=0", (digest,)).fetchone()
        if not key:
            return jsonify({"error": "invalid_api_key", "message": "The API key is invalid or revoked."}), 401
        g.api_user_id = key["user_id"]
        sub_row = db.execute("""SELECT s.*,p.name AS plan_name,p.lookup_limit,p.active AS plan_active,
                                       COALESCE(p.image_access,0) AS image_access
                               FROM subscriptions s JOIN plans p ON p.id=s.plan_id
                               WHERE s.user_id=? AND s.starts_at<=? AND s.expires_at>?
                               ORDER BY s.expires_at DESC LIMIT 1""", (key["user_id"], now_iso(), now_iso())).fetchone()
        if not sub_row:
            db.execute("INSERT INTO api_logs(user_id,barcode,status_code,created_at) VALUES(?,?,?,?)",
                       (key["user_id"], normalize_barcode(barcode)[:50], 402, now_iso()))
            return jsonify({"error": "subscription_required", "message": "An active plan is required."}), 402
        sub = dict(sub_row)
        used = db.execute("SELECT COUNT(*) FROM api_logs WHERE user_id=? AND created_at>=? AND created_at<? AND status_code != 429",
                          (key["user_id"], sub["starts_at"], sub["expires_at"])).fetchone()[0]
        if used >= int(sub["lookup_limit"]):
            db.execute("INSERT INTO api_logs(user_id,barcode,status_code,created_at) VALUES(?,?,?,?)",
                       (key["user_id"], normalize_barcode(barcode)[:50], 429, now_iso()))
            return jsonify({"error": "limit_reached", "message": "Your plan's lookup limit has been reached."}), 429
        code = normalize_barcode(barcode)[:50]
        product = db.execute("SELECT * FROM products WHERE barcode=?", (code,)).fetchone()
        status = 200 if product else 404
        db.execute("INSERT INTO api_logs(user_id,barcode,status_code,created_at) VALUES(?,?,?,?)",
                   (key["user_id"], code, status, now_iso()))
        if not product:
            return jsonify({"error": "product_not_found", "barcode": code, "message": "No matching product was found."}), 404
        p = dict(product)
    result = {
        "barcode": p["barcode"], "name": p["name"], "brand": p["brand"],
        "pack_size": p["pack_size"], "category": p["category"],
        "price_kes": p["price_kes"], "verified": bool(p["verified"]),
        "source": p["source"], "updated_at": p["updated_at"],
        "usage": {"limit": int(sub["lookup_limit"]), "used": int(used) + 1,
                  "remaining": max(0, int(sub["lookup_limit"]) - int(used) - 1)}
    }
    if bool(sub.get("image_access")):
        image_url = (p.get("image_url") or "").strip()
        if image_url.startswith("/"):
            image_url = request.url_root.rstrip("/") + image_url
        result["image_url"] = image_url or None
        result["image_available"] = bool(image_url)
        result["image_source"] = (p.get("image_source") or None) if image_url else None
    return jsonify(result)


@app.route("/health")
def health():
    try:
        with connect_db() as db:
            integrity = db.execute("PRAGMA quick_check").fetchone()[0]
            products = int(db.execute("SELECT COUNT(*) FROM products").fetchone()[0])
            images = int(db.execute("SELECT COUNT(*) FROM products WHERE COALESCE(TRIM(image_url),'')<>''").fetchone()[0])
            settings = get_settings(db)
        if integrity != "ok":
            raise sqlite3.DatabaseError("SQLite quick check did not return ok")
        return jsonify({"status": "ok", "service": "Kenya Barcode Hub", "database": "ok",
                       "api": "enabled" if settings.get("api_enabled", "1") == "1" else "disabled",
                       "products": products, "products_with_images": images}), 200
    except Exception:
        app.logger.exception("Health check failed")
        return jsonify({"status": "degraded", "service": "Kenya Barcode Hub", "database": "error"}), 503


@app.route("/media/products/<path:filename>")
def product_image_file(filename):
    if not re.fullmatch(r"[a-f0-9]{20}\.(?:jpg|png|webp|gif)", filename):
        abort(404)
    return send_from_directory(PRODUCT_IMAGE_DIR, filename, max_age=604800, conditional=True)


@app.route("/offline")
def offline():
    return render_template("offline.html")


@app.route("/manifest.webmanifest")
def manifest():
    response = make_response(send_file(BASE_DIR / "static" / "manifest.webmanifest", mimetype="application/manifest+json"))
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@app.route("/sw.js")
def service_worker():
    response = make_response(send_file(BASE_DIR / "static" / "sw.js", mimetype="application/javascript"))
    response.headers["Service-Worker-Allowed"] = "/"
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/favicon.ico")
def favicon():
    return send_file(BASE_DIR / "static" / "icons" / "favicon.svg", mimetype="image/svg+xml")


@app.route("/robots.txt")
def robots():
    return "User-agent: *\nAllow: /\nDisallow: /promise212324\nDisallow: /account\nDisallow: /api/\nSitemap: " + request.url_root.rstrip("/") + "/sitemap.xml\n", 200, {"Content-Type": "text/plain; charset=utf-8"}


@app.route("/sitemap.xml")
def sitemap():
    base = request.url_root.rstrip("/")
    paths = ["/", "/catalogue", "/pricing", "/docs", "/privacy"]
    body = "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n<urlset xmlns=\"http://www.sitemaps.org/schemas/sitemap/0.9\">"
    body += "".join(f"<url><loc>{base}{path}</loc></url>" for path in paths)
    body += "</urlset>"
    return body, 200, {"Content-Type": "application/xml; charset=utf-8"}


# ------------------------------ Admin area ------------------------------

ADMIN_PREFIX = "/promise212324"
_admin_attempts: dict[str, list[float]] = {}


def admin_rate_limited(ip: str) -> bool:
    now = time.time()
    attempts = [t for t in _admin_attempts.get(ip, []) if now - t < 900]
    _admin_attempts[ip] = attempts
    return len(attempts) >= 8


def admin_record_failure(ip: str) -> None:
    _admin_attempts.setdefault(ip, []).append(time.time())


@app.route(ADMIN_PREFIX, methods=["GET", "POST"])
def admin_login():
    if is_admin():
        return redirect(url_for("admin_dashboard"))
    if request.method == "POST":
        ip = request.remote_addr or "unknown"
        if admin_rate_limited(ip):
            log_system_event("admin_auth", "admin_login_rate_limited", "Admin sign-in throttled after repeated failed attempts",
                             429, None, request.path, "warning")
            flash("Too many sign-in attempts. Wait 15 minutes and try again.", "error")
            return render_template("admin_login.html")
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if admin_credentials_ok(username, password):
            _admin_attempts.pop(ip, None)
            session.clear()
            session["is_admin"] = True
            session["admin_user"] = os.environ.get("ADMIN_USERNAME", "Admin").strip()
            session["admin_password_fingerprint"] = hashlib.sha256(os.environ.get("ADMIN_PASSWORD", "").encode()).hexdigest()
            csrf_token()
            return redirect(url_for("admin_dashboard"))
        admin_record_failure(ip)
        log_system_event("admin_auth", "admin_login_failed", "Admin sign-in credentials were rejected",
                         401, None, request.path, "warning")
        time.sleep(0.25)
        flash("Admin username or password is incorrect. Confirm ADMIN_USERNAME and ADMIN_PASSWORD in Render.", "error")
    return render_template("admin_login.html")


@app.post(ADMIN_PREFIX + "/logout")
@admin_required
def admin_logout():
    session.clear()
    flash("Admin signed out.", "info")
    return redirect(url_for("admin_login"))


BACKUP_FORMAT = "kenya-barcode-hub-full-backup"
BACKUP_VERSION = 2
LEGACY_BACKUP_TABLES = ("users", "products", "posts", "settings", "plans", "orders", "subscriptions", "api_keys", "api_logs", "visitor_events")
BACKUP_TABLES = LEGACY_BACKUP_TABLES + ("system_events",)
DELETE_ORDER = ("system_events", "subscriptions", "api_keys", "api_logs", "orders", "visitor_events", "products", "posts", "settings", "plans", "users")
INSERT_ORDER = ("users", "plans", "products", "posts", "settings", "orders", "subscriptions", "api_keys", "api_logs", "visitor_events", "system_events")

LEGACY_BACKUP_COLUMNS = {
    "users": ["id", "name", "email", "password_hash", "created_at"],
    "products": ["id", "barcode", "name", "brand", "pack_size", "category", "price_kes", "verified", "source", "updated_at"],
    "posts": ["id", "title", "summary", "url", "kind", "premium", "active", "created_at"],
    "settings": ["key", "value"],
    "plans": ["id", "slug", "name", "price_kes", "lookup_limit", "duration_days", "description", "active"],
    "orders": ["id", "user_id", "plan_id", "amount_kes", "payer_name", "payer_phone", "transaction_code", "status", "admin_note", "created_at", "reviewed_at"],
    "subscriptions": ["id", "user_id", "plan_id", "starts_at", "expires_at", "order_id"],
    "api_keys": ["id", "user_id", "label", "prefix", "token_hash", "created_at", "revoked"],
    "api_logs": ["id", "user_id", "barcode", "status_code", "created_at"],
    "visitor_events": ["id", "visitor_id", "path", "method", "ip_masked", "referrer", "user_agent", "created_at"],
}

# A later pre-image build also linked some visitor events to customer IDs. Accept
# both older schemas so this field and those historical links are preserved.
LEGACY_BACKUP_COLUMNS_WITH_VISITOR_USER = {
    **LEGACY_BACKUP_COLUMNS,
    "visitor_events": LEGACY_BACKUP_COLUMNS["visitor_events"] + ["user_id"],
}


def create_system_events_schema(db):
    db.execute("""CREATE TABLE IF NOT EXISTS system_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type TEXT NOT NULL,
        severity TEXT NOT NULL DEFAULT 'info',
        source TEXT NOT NULL,
        message TEXT NOT NULL,
        status_code INTEGER,
        user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
        path TEXT DEFAULT '',
        created_at TEXT NOT NULL
    )""")
    db.execute("CREATE INDEX IF NOT EXISTS idx_system_events_date ON system_events(created_at)")
    db.execute("CREATE INDEX IF NOT EXISTS idx_system_events_source_status ON system_events(source,status_code)")


def migrate_legacy_sqlite_backup(path: Path) -> None:
    """Upgrade a known pre-image .sqlite3 backup in-place before strict validation."""
    raw = sqlite3.connect(path, timeout=20)
    try:
        quick = raw.execute("PRAGMA quick_check").fetchone()
        if not quick or quick[0] != "ok":
            return
        tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if tables != set(LEGACY_BACKUP_TABLES):
            return
        if raw.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view') LIMIT 1").fetchone():
            return
        old_schema = {table: [r[1] for r in raw.execute(f'PRAGMA table_info("{table}")')] for table in LEGACY_BACKUP_TABLES}
        if old_schema not in (LEGACY_BACKUP_COLUMNS, LEGACY_BACKUP_COLUMNS_WITH_VISITOR_USER):
            return
        raw.execute("ALTER TABLE products ADD COLUMN image_url TEXT DEFAULT ''")
        raw.execute("ALTER TABLE products ADD COLUMN image_source TEXT DEFAULT ''")
        raw.execute("ALTER TABLE products ADD COLUMN image_checked_at TEXT DEFAULT ''")
        raw.execute("ALTER TABLE plans ADD COLUMN image_access INTEGER NOT NULL DEFAULT 0")
        raw.execute("ALTER TABLE users ADD COLUMN password_changed_at TEXT DEFAULT ''")
        raw.execute("CREATE INDEX IF NOT EXISTS idx_products_image_url ON products(image_url)")
        visitor_columns = {r[1] for r in raw.execute("PRAGMA table_info(visitor_events)")}
        if "user_id" not in visitor_columns:
            raw.execute("ALTER TABLE visitor_events ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE SET NULL")
        raw.execute("CREATE INDEX IF NOT EXISTS idx_visitors_user ON visitor_events(user_id)")
        create_system_events_schema(raw)
        raw.commit()
    finally:
        raw.close()


def database_columns(connection, table_name):
    # table_name is always chosen from BACKUP_TABLES.
    return [row["name"] if isinstance(row, sqlite3.Row) else row[1]
            for row in connection.execute(f'PRAGMA table_info("{table_name}")')]


def live_database_schema():
    db = sqlite3.connect(DB_PATH)
    try:
        return {table: database_columns(db, table) for table in BACKUP_TABLES}
    finally:
        db.close()


def snapshot_database(source_path: Path, destination_path: Path):
    """Use SQLite's online backup API so a download is a consistent snapshot."""
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(source_path, timeout=30)
    target = sqlite3.connect(destination_path, timeout=30)
    try:
        source.backup(target, pages=256, sleep=0.05)
        check = target.execute("PRAGMA integrity_check").fetchone()
        if not check or check[0] != "ok":
            raise sqlite3.DatabaseError("Snapshot integrity check failed")
    finally:
        target.close()
        source.close()


def validate_sqlite_backup(path: Path):
    expected = live_database_schema()
    uri = path.resolve().as_uri() + "?mode=ro"
    db = sqlite3.connect(uri, uri=True, timeout=20)
    try:
        check = db.execute("PRAGMA quick_check").fetchone()
        if not check or check[0] != "ok":
            raise ValueError("The SQLite backup failed its integrity check.")
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if tables != set(BACKUP_TABLES):
            raise ValueError("This file is not a complete Kenya Barcode Hub database backup (tables do not match).")
        suspicious = db.execute("SELECT type,name FROM sqlite_master WHERE type IN ('trigger','view')").fetchall()
        if suspicious:
            raise ValueError("The uploaded database contains unsupported triggers or views; it was not restored.")
        def explicit_indexes(connection):
            return {row[0]: row[1] for row in connection.execute("SELECT name,sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
        live_db = sqlite3.connect(DB_PATH)
        try:
            expected_indexes = explicit_indexes(live_db)
        finally:
            live_db.close()
        if explicit_indexes(db) != expected_indexes:
            raise ValueError("The backup indexes do not match this version of the site.")
        for table in BACKUP_TABLES:
            if database_columns(db, table) != expected[table]:
                raise ValueError(f"The backup schema for {table} does not match this version of the site.")
        fk_errors = db.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise ValueError("The uploaded database has broken relationships; it was not restored.")
    finally:
        db.close()


def make_safety_backup() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    path = DB_PATH.parent / f"barcode-hub-safety-{stamp}.sqlite3"
    snapshot_database(DB_PATH, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    safety = sorted(DB_PATH.parent.glob("barcode-hub-safety-*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old_path in safety[3:]:
        try:
            old_path.unlink()
        except OSError:
            app.logger.warning("Could not prune old safety backup: %s", old_path)
    return path


def validate_json_backup(payload):
    if not isinstance(payload, dict) or payload.get("format") != BACKUP_FORMAT:
        raise ValueError("This JSON file is not a supported full-site backup.")
    version = payload.get("version")
    tables = payload.get("tables")
    if version == 1:
        if not isinstance(tables, dict) or set(tables) != set(LEGACY_BACKUP_TABLES):
            raise ValueError("The legacy JSON backup is incomplete; all original site tables are required.")
        upgraded_tables = {}
        for table in LEGACY_BACKUP_TABLES:
            group = tables.get(table)
            incoming_columns = group.get("columns") if isinstance(group, dict) else None
            allowed_columns = [LEGACY_BACKUP_COLUMNS[table]]
            if table == "visitor_events":
                allowed_columns.append(LEGACY_BACKUP_COLUMNS_WITH_VISITOR_USER[table])
            if not isinstance(group, dict) or incoming_columns not in allowed_columns or not isinstance(group.get("rows"), list):
                raise ValueError(f"The legacy JSON backup has an invalid {table} section.")
            extended_rows = []
            for row in group["rows"]:
                if not isinstance(row, list) or len(row) != len(incoming_columns):
                    raise ValueError(f"A legacy {table} row has the wrong number of fields.")
                new_row = list(row)
                if table == "products":
                    new_row.extend(["", "", ""])
                elif table == "plans":
                    new_row.append(0)
                elif table == "users":
                    new_row.append("")
                elif table == "visitor_events" and "user_id" not in incoming_columns:
                    new_row.append(None)
                extended_rows.append(new_row)
            upgraded_columns = list(LEGACY_BACKUP_COLUMNS[table]) + (
                ["image_url", "image_source", "image_checked_at"] if table == "products" else
                ["image_access"] if table == "plans" else
                ["password_changed_at"] if table == "users" else
                ["user_id"] if table == "visitor_events" else [])
            upgraded_tables[table] = {"columns": upgraded_columns, "rows": extended_rows}
        upgraded_tables["system_events"] = {"columns": ["id", "event_type", "severity", "source", "message", "status_code", "user_id", "path", "created_at"], "rows": []}
        payload = {"format": BACKUP_FORMAT, "version": BACKUP_VERSION,
                   "created_at": payload.get("created_at", now_iso()), "tables": upgraded_tables}
        tables = payload["tables"]
    elif version != BACKUP_VERSION:
        raise ValueError("This backup version is not supported.")
    if not isinstance(tables, dict) or set(tables) != set(BACKUP_TABLES):
        raise ValueError("The JSON backup is incomplete; all site tables are required.")
    expected = live_database_schema()
    max_rows = 2_000_000
    total = 0
    for table in BACKUP_TABLES:
        group = tables.get(table)
        if not isinstance(group, dict) or group.get("columns") != expected[table] or not isinstance(group.get("rows"), list):
            raise ValueError(f"The JSON backup has an invalid or incompatible {table} section.")
        total += len(group["rows"])
        if total > max_rows:
            raise ValueError("The JSON backup contains too many records to restore safely.")
        for row in group["rows"]:
            if not isinstance(row, list) or len(row) != len(expected[table]):
                raise ValueError(f"A row in the {table} section has the wrong number of fields.")
            if any(isinstance(v, (dict, list)) for v in row):
                raise ValueError(f"A row in the {table} section contains invalid field data.")
    return payload


def restore_json_to_database(payload, candidate_path: Path):
    # Called only under the exclusive database lock. Clone current schema/indexes,
    # then atomically replace every application table in dependency order.
    snapshot_database(DB_PATH, candidate_path)
    db = sqlite3.connect(candidate_path, timeout=30)
    try:
        db.execute("PRAGMA foreign_keys = OFF")
        db.execute("BEGIN IMMEDIATE")
        for table in DELETE_ORDER:
            db.execute(f'DELETE FROM "{table}"')
        for table in INSERT_ORDER:
            group = payload["tables"][table]
            columns = group["columns"]
            if not columns:
                continue
            quoted_columns = ",".join('"' + c.replace('"', '""') + '"' for c in columns)
            marks = ",".join("?" for _ in columns)
            sql = f'INSERT INTO "{table}" ({quoted_columns}) VALUES ({marks})'
            if group["rows"]:
                db.executemany(sql, group["rows"])
        db.commit()
        db.execute("PRAGMA foreign_keys = ON")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("The restored JSON database failed its integrity check.")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("The restored JSON database has broken record relationships.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@app.route(ADMIN_PREFIX + "/backup")
@admin_required
def admin_database_backup():
    # Never expose the live SQLite file directly; create a consistent snapshot.
    fd, temp_name = tempfile.mkstemp(prefix="barcode-hub-backup-", suffix=".sqlite3")
    os.close(fd)
    temp_path = Path(temp_name)
    try:
        with database_file_lock(exclusive=False):
            snapshot_database(DB_PATH, temp_path)
        response = send_file(temp_path, mimetype="application/vnd.sqlite3", as_attachment=True,
                             download_name=f"barcode-hub-backup-{datetime.now(timezone.utc).date().isoformat()}.sqlite3", max_age=0)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.call_on_close(lambda: temp_path.unlink(missing_ok=True))
        return response
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


@app.route(ADMIN_PREFIX + "/backup.json")
@admin_required
def admin_database_backup_json():
    try:
        with connect_db() as db:
            db.execute("BEGIN")  # Snapshot all JSON table reads in one SQLite read transaction.
            integrity = db.execute("PRAGMA quick_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError("Database integrity check did not pass.")
            tables = {}
            for table in BACKUP_TABLES:
                columns = database_columns(db, table)
                rows = [list(row) for row in db.execute(f'SELECT * FROM "{table}"')]
                tables[table] = {"columns": columns, "rows": rows}
        payload = {"format": BACKUP_FORMAT, "version": BACKUP_VERSION, "created_at": now_iso(), "tables": tables}
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
        response = make_response(send_file(io.BytesIO(raw), mimetype="application/json", as_attachment=True,
                                           download_name=f"barcode-hub-backup-{datetime.now(timezone.utc).date().isoformat()}.json", max_age=0))
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return response
    except Exception:
        app.logger.exception("Could not create full JSON backup")
        abort(500, description="The JSON backup could not be created. No records were changed.")


@app.route(ADMIN_PREFIX + "/backups/safety/<filename>")
@admin_required
def admin_safety_backup_download(filename):
    if not re.fullmatch(r"barcode-hub-safety-\d{8}-\d{6}-\d{6}\.sqlite3", filename):
        abort(404)
    path = DB_PATH.parent / filename
    if not path.is_file() or path.parent.resolve() != DB_PATH.parent.resolve():
        abort(404)
    response = send_file(path, mimetype="application/vnd.sqlite3", as_attachment=True, download_name=filename, max_age=0)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@app.route(ADMIN_PREFIX + "/backups", methods=["GET", "POST"])
@admin_required
def admin_backups():
    if request.method == "POST":
        upload = request.files.get("backup_file")
        if not upload or not upload.filename:
            flash("Choose a full-site .sqlite3, .db or .json backup file.", "error")
            return redirect(url_for("admin_backups"))
        suffix = Path(upload.filename).suffix.lower()
        if suffix not in (".sqlite3", ".db", ".json"):
            flash("Unsupported backup type. Use .sqlite3, .db or a full-site .json backup.", "error")
            return redirect(url_for("admin_backups"))
        payload = None
        candidate_path = None
        try:
            raw = upload.stream.read()
            if not raw:
                raise ValueError("The selected backup file is empty.")
            if suffix == ".json":
                parsed_json = json.loads(raw.decode("utf-8-sig"), parse_constant=lambda value: (_ for _ in ()).throw(ValueError("Non-finite JSON numbers are not allowed.")))
                with database_file_lock(exclusive=False):
                    payload = validate_json_backup(parsed_json)
            else:
                fd, filename = tempfile.mkstemp(prefix="barcode-hub-restore-", suffix=".sqlite3", dir=str(DB_PATH.parent))
                candidate_path = Path(filename)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(raw)
                with database_file_lock(exclusive=False):
                    migrate_legacy_sqlite_backup(candidate_path)
                    validate_sqlite_backup(candidate_path)

            # Lock out all other app DB connections while saving the safety copy
            # and replacing the live file. The uploaded file is not trusted until
            # it passed format/schema/integrity/foreign-key checks above.
            with database_file_lock(exclusive=True):
                if payload is not None:
                    fd, filename = tempfile.mkstemp(prefix="barcode-hub-restore-json-", suffix=".sqlite3", dir=str(DB_PATH.parent))
                    os.close(fd)
                    candidate_path = Path(filename)
                    restore_json_to_database(payload, candidate_path)
                    validate_sqlite_backup(candidate_path)
                safety_path = make_safety_backup()
                for suffix_sidecar in ("-wal", "-shm"):
                    try:
                        Path(str(DB_PATH) + suffix_sidecar).unlink(missing_ok=True)
                    except OSError:
                        app.logger.warning("Could not remove SQLite sidecar %s", suffix_sidecar)
                os.replace(candidate_path, DB_PATH)
                candidate_path = None
            flash(f"Restore complete. A safety copy of the previous database was saved as {safety_path.name}.", "success")
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError, sqlite3.Error, OSError) as exc:
            app.logger.warning("Database restore rejected or failed: %s", exc)
            flash(f"Restore stopped safely: {str(exc)[:240]}", "error")
        except Exception:
            app.logger.exception("Unexpected database restore failure")
            flash("Restore failed. The live database was not intentionally replaced; check server logs and try a fresh backup.", "error")
        finally:
            if candidate_path is not None:
                try:
                    candidate_path.unlink(missing_ok=True)
                except OSError:
                    pass
        return redirect(url_for("admin_backups"))
    safety_files = sorted(DB_PATH.parent.glob("barcode-hub-safety-*.sqlite3"), key=lambda p: p.stat().st_mtime, reverse=True)[:3]
    safety_backups = [{"name": p.name, "size": p.stat().st_size,
                       "modified": datetime.fromtimestamp(p.stat().st_mtime, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}
                      for p in safety_files]
    return render_template("admin_backups.html", safety_backups=safety_backups, database_path=str(DB_PATH),
                           database_bytes=DB_PATH.stat().st_size if DB_PATH.exists() else 0)


@app.route(ADMIN_PREFIX + "/dashboard")
@admin_required
def admin_dashboard():
    since = (datetime.now(timezone.utc) - timedelta(hours=24)).replace(microsecond=0).isoformat()
    with connect_db() as db:
        stats = {
            "products": db.execute("SELECT COUNT(*) FROM products").fetchone()[0],
            "verified": db.execute("SELECT COUNT(*) FROM products WHERE verified=1").fetchone()[0],
            "images": db.execute("SELECT COUNT(*) FROM products WHERE COALESCE(TRIM(image_url),'')<>''").fetchone()[0],
            "missing_images": db.execute("SELECT COUNT(*) FROM products WHERE COALESCE(TRIM(image_url),'')=''").fetchone()[0],
            "users": db.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "pending": db.execute("SELECT COUNT(*) FROM orders WHERE status='pending'").fetchone()[0],
            "api_calls_24h": db.execute("SELECT COUNT(*) FROM system_events WHERE source='api' AND created_at>=?", (since,)).fetchone()[0],
            "api_errors_24h": db.execute("SELECT COUNT(*) FROM system_events WHERE source='api' AND status_code>=400 AND created_at>=?", (since,)).fetchone()[0],
            # Dashboard traffic is session-based, not a raw request/ping count.
            "visits_today": db.execute("SELECT COUNT(DISTINCT visitor_id) FROM visitor_events WHERE created_at >= ?", (datetime.now(timezone.utc).date().isoformat(),)).fetchone()[0],
            "unique_today": db.execute("SELECT COUNT(DISTINCT visitor_id) FROM visitor_events WHERE created_at >= ?", (datetime.now(timezone.utc).date().isoformat(),)).fetchone()[0],
        }
        recent_orders = [dict(r) for r in db.execute("""SELECT o.*,u.name AS user_name,u.email,p.name AS plan_name FROM orders o
                    JOIN users u ON u.id=o.user_id JOIN plans p ON p.id=o.plan_id ORDER BY o.id DESC LIMIT 8""")]
        recent_visitors = [dict(r) for r in db.execute("SELECT * FROM visitor_events ORDER BY id DESC LIMIT 10")]
    return render_template("admin_dashboard.html", stats=stats, orders=recent_orders, visitors=recent_visitors,
                           persistent_storage=str(DB_PATH).startswith("/var/data/"), database_path=str(DB_PATH))


@app.route(ADMIN_PREFIX + "/products", methods=["GET", "POST"])
@admin_required
def admin_products():
    if request.method == "POST":
        action = request.form.get("action", "add")
        if action in ("import_csv", "import_catalogue"):
            file = request.files.get("file")
            if not file or Path(file.filename).suffix.lower() not in (".csv", ".json"):
                flash("Choose a catalogue CSV or product JSON file.", "error")
                return redirect(url_for("admin_products"))
            try:
                content = file.stream.read()
                if Path(file.filename).suffix.lower() == ".json":
                    payload = json.loads(content.decode("utf-8-sig"))
                    records = payload.get("products") if isinstance(payload, dict) else payload
                    if not isinstance(records, list) or (isinstance(payload, dict) and payload.get("format") not in (None, "kenya-barcode-hub-products")):
                        raise ValueError("Not a supported product catalogue JSON file.")
                    if len(records) > 200000:
                        raise ValueError("The catalogue contains too many product records.")
                    reader = records
                else:
                    reader = csv.DictReader(io.StringIO(content.decode("utf-8-sig", errors="replace")))
                imported, updated, skipped, invalid_images = 0, 0, 0, 0
                with connect_db() as db:
                    for raw in reader:
                        if not isinstance(raw, dict):
                            skipped += 1
                            continue
                        row = {re.sub(r"[^a-z0-9]+", "_", str(k or "").strip().lower()).strip("_"): ("" if v is None else str(v).strip()) for k, v in raw.items() if k}
                        def pick(*names):
                            for name in names:
                                if row.get(name):
                                    return row[name]
                            return ""
                        barcode = normalize_barcode(pick("barcode", "barcode_gtin", "product_barcode", "gtin", "upc", "ean", "ean13", "code", "product_code"))
                        name = pick("name", "product", "product_name", "description", "item")
                        if not barcode or not name:
                            skipped += 1
                            continue
                        if len(barcode) > 50 or len(name) > 200:
                            skipped += 1
                            continue
                        price_text = pick("price_kes", "price_ksh", "price_kes_ksh", "price", "selling_price", "amount_price", "retail_price")
                        try:
                            price_match = re.search(r"-?\d+(?:\.\d+)?", price_text.replace(",", ""))
                            price = float(price_match.group(0)) if price_match else None
                            if price is not None and price < 0:
                                price = None
                        except ValueError:
                            price = None
                        verified_text = pick("verified", "is_verified", "verification_status").lower()
                        verified = 1 if verified_text in ("1", "true", "yes", "verified", "y") else 0
                        brand = pick("brand", "manufacturer")
                        pack_size = pick("pack_size", "size", "amount", "quantity", "unit")
                        category = pick("category", "department", "group")
                        source = pick("source", "source_url", "supplier")
                        image_url = pick("image_url", "image", "product_image", "image_link", "image_url_link")[:2000]
                        if image_url and not valid_image_url(image_url):
                            image_url = ""
                            invalid_images += 1
                        exists = db.execute("SELECT id FROM products WHERE barcode=?", (barcode,)).fetchone()
                        db.execute("""INSERT INTO products(barcode,name,brand,pack_size,category,price_kes,verified,source,updated_at,image_url,image_source,image_checked_at)
                                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(barcode) DO UPDATE SET
                                      name=excluded.name,brand=excluded.brand,pack_size=excluded.pack_size,category=excluded.category,
                                      price_kes=COALESCE(excluded.price_kes, products.price_kes),verified=excluded.verified,source=excluded.source,updated_at=excluded.updated_at,
                                      image_url=CASE WHEN excluded.image_url<>'' THEN excluded.image_url ELSE products.image_url END,
                                      image_source=CASE WHEN excluded.image_url<>'' THEN excluded.image_source ELSE products.image_source END,
                                      image_checked_at=CASE WHEN excluded.image_url<>'' THEN excluded.image_checked_at ELSE products.image_checked_at END""",
                                   (barcode, name[:200], brand[:120], pack_size[:100], category[:100], price, verified, source[:600], now_iso(),
                                    image_url, "catalogue import" if image_url else "", now_iso() if image_url else ""))
                        if exists:
                            updated += 1
                        else:
                            imported += 1
                msg = f"Import complete: {imported} new, {updated} updated, {skipped} skipped."
                if invalid_images:
                    msg += f" {invalid_images} invalid image links were ignored."
                flash(msg, "success")
            except Exception as exc:
                app.logger.exception("Catalogue CSV import failed")
                flash("The CSV could not be imported. Check its columns and encoding.", "error")
            return redirect(url_for("admin_products"))
        barcode = normalize_barcode(request.form.get("barcode", ""))
        name = request.form.get("name", "").strip()[:200]
        brand = request.form.get("brand", "").strip()[:120]
        pack_size = request.form.get("pack_size", "").strip()[:100]
        category = request.form.get("category", "").strip()[:100]
        price_text = request.form.get("price_kes", "").strip()
        source = request.form.get("source", "").strip()[:300]
        image_url = request.form.get("image_url", "").strip()[:2000]
        image_file = request.files.get("image_file")
        verified = 1 if request.form.get("verified") == "on" else 0
        try:
            price_match = re.search(r"-?\d+(?:\.\d+)?", price_text.replace(",", ""))
            price = float(price_match.group(0)) if price_match else None
            if price is not None and price < 0:
                price = None
        except ValueError:
            price = None
        if not barcode or not name:
            flash("Barcode and product name are required.", "error")
        elif len(barcode) > 50:
            flash("Barcode is too long.", "error")
        elif image_url and not valid_image_url(image_url):
            flash("Use a complete http/https image URL or upload a local image.", "error")
        else:
            try:
                if image_file and image_file.filename:
                    image_url = save_product_image(image_file, barcode)
                    image_source = "admin upload"
                    image_checked_at = now_iso()
                elif image_url:
                    image_source = "manual image URL"
                    image_checked_at = now_iso()
                else:
                    image_source = ""
                    image_checked_at = ""
                with connect_db() as db:
                    db.execute("""INSERT INTO products(barcode,name,brand,pack_size,category,price_kes,verified,source,updated_at,image_url,image_source,image_checked_at)
                                  VALUES(?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(barcode) DO UPDATE SET
                                  name=excluded.name,brand=excluded.brand,pack_size=excluded.pack_size,category=excluded.category,
                                  price_kes=excluded.price_kes,verified=excluded.verified,source=excluded.source,updated_at=excluded.updated_at,
                                  image_url=CASE WHEN excluded.image_url<>'' THEN excluded.image_url ELSE products.image_url END,
                                  image_source=CASE WHEN excluded.image_url<>'' THEN excluded.image_source ELSE products.image_source END,
                                  image_checked_at=CASE WHEN excluded.image_url<>'' THEN excluded.image_checked_at ELSE products.image_checked_at END""",
                               (barcode, name, brand, pack_size, category, price, verified, source, now_iso(), image_url, image_source, image_checked_at))
                flash("Product saved.", "success")
            except ValueError as exc:
                flash(str(exc), "error")
            except sqlite3.Error:
                flash("Could not save this product. Check the barcode and try again.", "error")
        return redirect(url_for("admin_products"))
    q = request.args.get("q", "")[:100]
    results = query_products(q, 100) if q else []
    with connect_db() as db:
        count = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    return render_template("admin_products.html", results=results, q=q, count=count)


def safe_csv_value(value):
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


@app.route(ADMIN_PREFIX + "/products.csv")
@admin_required
def admin_export_products():
    with connect_db() as db:
        rows = db.execute("SELECT barcode,name,brand,pack_size,category,price_kes,verified,source,updated_at,image_url,image_source,image_checked_at FROM products ORDER BY name COLLATE NOCASE").fetchall()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["barcode", "name", "brand", "pack_size", "category", "price_kes", "verified", "source", "updated_at", "image_url", "image_source", "image_checked_at"])
    exported = []
    for row in rows:
        values = list(row)
        if values[9] and str(values[9]).startswith("/"):
            values[9] = request.url_root.rstrip("/") + str(values[9])
        exported.append([safe_csv_value(v) for v in values])
    writer.writerows(exported)
    response = make_response("\ufeff" + output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = 'attachment; filename="kenya-barcode-hub-catalogue-backup.csv"'
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post(ADMIN_PREFIX + "/products/<int:product_id>/delete")
@admin_required
def admin_delete_product(product_id):
    with connect_db() as db:
        row = db.execute("SELECT image_url FROM products WHERE id=?", (product_id,)).fetchone()
        if not row:
            flash("Product was not found.", "error")
            return redirect(url_for("admin_products"))
        image_url = row["image_url"] or ""
        db.execute("DELETE FROM products WHERE id=?", (product_id,))
        if image_url.startswith("/media/products/"):
            filename = image_url.rsplit("/", 1)[-1]
            still_used = db.execute("SELECT 1 FROM products WHERE image_url=? LIMIT 1", (image_url,)).fetchone()
            if not still_used and re.fullmatch(r"[a-f0-9]{20}\.(?:jpg|png|webp|gif)", filename):
                try:
                    (PRODUCT_IMAGE_DIR / filename).unlink(missing_ok=True)
                except OSError:
                    app.logger.warning("Could not remove unreferenced product image %s", filename)
    flash("Product deleted.", "success")
    return redirect(url_for("admin_products"))


def _image_url_from_public_product(product):
    """Return an explicit product-front image URL from common OFF API response shapes."""
    if not isinstance(product, dict):
        return ""
    for key in ("image_front_url", "image_url", "image_front_small_url", "image_small_url", "image_thumb_url"):
        candidate = product.get(key)
        if isinstance(candidate, str) and candidate.startswith(("https://", "http://")):
            return candidate.strip()
    selected = product.get("selected_images") or {}
    if isinstance(selected, dict):
        front = selected.get("front") or {}
        if isinstance(front, dict):
            for size in ("display", "small", "thumb"):
                images = front.get(size)
                if isinstance(images, str) and images.startswith(("https://", "http://")):
                    return images.strip()
                if isinstance(images, dict):
                    # Prefer English, but support products indexed in other languages.
                    ordered = [images.get("en")] + list(images.values())
                    for candidate in ordered:
                        if isinstance(candidate, str) and candidate.startswith(("https://", "http://")):
                            return candidate.strip()
    return ""


def lookup_public_product_image(barcode):
    """Search recognized public catalogues for an exact barcode; never guess by name."""
    code = normalize_barcode(barcode)
    catalogues = (
        ("Open Food Facts", "https://world.openfoodfacts.org"),
        ("Open Beauty Facts", "https://world.openbeautyfacts.org"),
        ("Open Products Facts", "https://world.openproductsfacts.org"),
    )
    unavailable = []
    for name, origin in catalogues:
        url = origin + "/api/v2/product/" + quote(code, safe="") + ".json?fields=code,product_name,image_front_url,image_url,image_front_small_url,image_small_url,image_thumb_url,selected_images"
        req = urllib.request.Request(url, headers={
            "User-Agent": "KenyaBarcodeHub/1.2 (product-image discovery; https://trorinovationstech.co.ke/)"
        })
        try:
            with urllib.request.urlopen(req, timeout=2.5) as remote:
                payload = json.loads(remote.read(2 * 1024 * 1024).decode("utf-8", errors="replace"))
            product = payload.get("product") if isinstance(payload, dict) else None
            if not isinstance(product, dict) or payload.get("status") != 1:
                continue
            returned_code = normalize_barcode(str(product.get("code") or payload.get("code") or ""))
            if not returned_code or returned_code != code:
                # Some catalogues normalize GTINs differently; do not risk a false match.
                continue
            candidate = _image_url_from_public_product(product)
            if candidate and valid_image_url(candidate):
                return candidate, name + " — " + origin + " (check source license before redistribution)", unavailable
        except urllib.error.HTTPError as exc:
            # HTTP 404 means this catalogue has no product result; rate-limit/server
            # responses are availability failures and should be shown to the admin.
            if exc.code not in (404,):
                unavailable.append(name)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
            unavailable.append(name)
    return "", "", unavailable


@app.route(ADMIN_PREFIX + "/images", methods=["GET", "POST"])
@admin_required
def admin_images():
    """Product-image coverage, manual linking, uploads and polite batch discovery."""
    if request.method == "POST":
        action = request.form.get("action", "")
        if action == "discover":
            cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).replace(microsecond=0).isoformat()
            cooldown_active = False
            retry_after = 0
            with connect_db() as db:
                # A small paced batch avoids hammering public product catalogues.
                db.execute("BEGIN IMMEDIATE")
                last_run = db.execute("SELECT value FROM settings WHERE key='_image_discovery_last_batch_at'").fetchone()
                if last_run:
                    try:
                        previous = datetime.fromisoformat(last_run["value"].replace("Z", "+00:00"))
                        elapsed = (datetime.now(timezone.utc) - previous).total_seconds()
                        cooldown_active = elapsed < 22
                        retry_after = max(1, int(22 - elapsed + 0.99)) if cooldown_active else 0
                    except (ValueError, TypeError):
                        cooldown_active = False
                if not cooldown_active:
                    db.execute("INSERT INTO settings(key,value) VALUES('_image_discovery_last_batch_at',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (now_iso(),))
                    candidates = [dict(r) for r in db.execute("""SELECT id,barcode,name FROM products
                        WHERE COALESCE(TRIM(image_url),'')=''
                          AND (COALESCE(TRIM(image_checked_at),'')='' OR image_checked_at<?)
                        ORDER BY CASE WHEN COALESCE(TRIM(image_checked_at),'')='' THEN 0 ELSE 1 END, id
                        LIMIT 3""", (cutoff,))]
                else:
                    candidates = []

            def discovery_result(status, message, found=0, checked=0, category="info", retry=0):
                with connect_db() as db:
                    total = int(db.execute("SELECT COUNT(*) FROM products").fetchone()[0])
                    linked = int(db.execute("SELECT COUNT(*) FROM products WHERE COALESCE(TRIM(image_url),'')<>''").fetchone()[0])
                    missing = max(0, total - linked)
                    remaining = int(db.execute("""SELECT COUNT(*) FROM products
                        WHERE COALESCE(TRIM(image_url),'')=''
                          AND (COALESCE(TRIM(image_checked_at),'')='' OR image_checked_at<?)""", (cutoff,)).fetchone()[0])
                if request.form.get("ajax") == "1":
                    return jsonify({"status": status, "message": message, "found": found,
                                    "checked": checked, "retry_after": retry,
                                    "total": total, "with_images": linked, "missing": missing,
                                    "remaining": remaining})
                flash(message, category)
                return redirect(url_for("admin_images", status="missing"))

            if cooldown_active:
                return discovery_result("cooldown", "Please wait before the next public-catalogue batch.", retry=retry_after)
            if not candidates:
                return discovery_result("complete", "The automatic pass has checked every queued record. Any remaining products need another source, an upload, or a trusted image URL.")

            found = 0
            checked = 0
            network_problems = 0
            outcomes = []
            for item in candidates:
                image_url, provider, incomplete_sources = lookup_public_product_image(item["barcode"])
                checked_at = now_iso()
                if image_url and valid_image_url(image_url):
                    with connect_db() as db:
                        db.execute("""UPDATE products SET image_url=?,image_source=?,image_checked_at=?,updated_at=?
                                      WHERE id=? AND COALESCE(TRIM(image_url),'')=''""",
                                   (image_url, provider, checked_at, checked_at, item["id"]))
                    found += 1
                    outcomes.append(f"{item['barcode']}: linked from {provider}")
                else:
                    # Keep unresolved records visible as missing. The check time means
                    # this record was attempted, never that an image exists.
                    status_note = ("Lookup incomplete: " + ", ".join(incomplete_sources)) if incomplete_sources else ""
                    with connect_db() as db:
                        db.execute("""UPDATE products SET image_checked_at=?,image_source=?,updated_at=?
                                      WHERE id=? AND COALESCE(TRIM(image_url),'')=''""",
                                   (checked_at, status_note, checked_at, item["id"]))
                    if incomplete_sources:
                        network_problems += 1
                    outcomes.append(f"{item['barcode']}: " + ("some public sources unavailable" if incomplete_sources else "no exact-barcode image found"))
                checked += 1

            log_system_event("images", "image_discovery_batch",
                             f"Image discovery checked {checked} records and linked {found} images; {network_problems} records had at least one unavailable source",
                             502 if network_problems else 200, None, request.path,
                             "warning" if network_problems else "info")
            if network_problems:
                message = f"Checked {checked}; linked {found} images. {network_problems} lookups had an unreachable public source and remain marked incomplete. Missing images are still counted as missing."
                category = "warning"
            else:
                message = f"Checked {checked}; linked {found} verified exact-barcode images. No image was invented for unmatched products."
                category = "success"
            return discovery_result("ok", message, found=found, checked=checked, category=category)

        if action == "retry_missing":
            with connect_db() as db:
                cursor = db.execute("UPDATE products SET image_checked_at='',image_source='' WHERE COALESCE(TRIM(image_url),'')=''")
                reset_count = cursor.rowcount
            log_system_event("images", "image_discovery_requeued", f"Requeued {reset_count} records for image discovery",
                             200, None, request.path, "info")
            flash(f"Requeued {reset_count} missing-image records. Start automatic image fill to check three records every 22 seconds.", "success")
            return redirect(url_for("admin_images", status="missing"))

        if action == "restore_assets":
            upload = request.files.get("image_archive")
            if not upload or not upload.filename or Path(upload.filename).suffix.lower() != ".zip":
                flash("Choose a product-image ZIP backup.", "error")
                return redirect(url_for("admin_images"))
            try:
                raw = upload.stream.read(128 * 1024 * 1024 + 1)
                if not raw or len(raw) > 128 * 1024 * 1024:
                    raise ValueError("The image archive is empty or exceeds the 128 MB upload limit.")
                restored_files = []
                total_bytes = 0
                with zipfile.ZipFile(io.BytesIO(raw), "r") as archive:
                    members = [info for info in archive.infolist() if not info.is_dir() and info.filename != "manifest.json"]
                    if len(members) > 20000:
                        raise ValueError("The archive contains too many image files.")
                    for info in members:
                        prefix, _, filename = info.filename.partition("/")
                        if prefix != "product-images" or not re.fullmatch(r"[a-f0-9]{20}\.(?:jpg|png|webp|gif)", filename):
                            raise ValueError("The archive contains an unexpected path or filename.")
                        if info.file_size > MAX_PRODUCT_IMAGE_BYTES:
                            raise ValueError("An image in the archive exceeds 8 MB.")
                        total_bytes += info.file_size
                        if total_bytes > 120 * 1024 * 1024:
                            raise ValueError("The total uncompressed image archive exceeds 120 MB.")
                        content = archive.read(info)
                        valid_magic = (content.startswith(b"\xff\xd8\xff") if filename.endswith(".jpg") else
                                       content.startswith(b"\x89PNG\r\n\x1a\n") if filename.endswith(".png") else
                                       (len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP") if filename.endswith(".webp") else
                                       content.startswith((b"GIF87a", b"GIF89a")))
                        if not valid_magic:
                            raise ValueError("An archived file failed image-content validation.")
                        restored_files.append((filename, content))
                PRODUCT_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
                for filename, content in restored_files:
                    (PRODUCT_IMAGE_DIR / filename).write_bytes(content)
                log_system_event("images", "image_assets_restored", f"Restored {len(restored_files)} product image files",
                                 200, None, request.path, "info")
                flash(f"Image asset restore complete: {len(restored_files)} files restored. Existing linked URLs were preserved.", "success")
            except (ValueError, zipfile.BadZipFile, OSError) as exc:
                log_system_event("images", "image_assets_restore_failed", "Image asset archive was rejected", 400, None, request.path, "warning")
                flash("Image restore stopped safely: " + str(exc)[:220], "error")
            return redirect(url_for("admin_images"))

        if action == "save_image":
            try:
                product_id = int(request.form.get("product_id", "0"))
            except ValueError:
                product_id = 0
            with connect_db() as db:
                row = db.execute("SELECT id,barcode,image_url FROM products WHERE id=?", (product_id,)).fetchone()
            if not row:
                flash("Product was not found.", "error")
                return redirect(url_for("admin_images"))
            old_url = row["image_url"] or ""
            image_url = request.form.get("image_url", "").strip()[:2000]
            upload = request.files.get("image_file")
            try:
                if request.form.get("clear_image") == "on":
                    image_url = ""
                    image_source = ""
                elif upload and upload.filename:
                    image_url = save_product_image(upload, row["barcode"])
                    image_source = "admin upload"
                elif image_url:
                    if not valid_image_url(image_url):
                        raise ValueError("Use a complete http/https image URL or upload a local image.")
                    image_source = "manual image URL"
                else:
                    flash("No change made. Enter an image URL, upload a file or select Remove image.", "info")
                    return redirect(url_for("admin_images", status=request.args.get("status", "missing"), q=request.args.get("q", ""), page=request.args.get("page", "1")))
                checked_at = now_iso()
                with connect_db() as db:
                    db.execute("UPDATE products SET image_url=?,image_source=?,image_checked_at=?,updated_at=? WHERE id=?",
                               (image_url, image_source, checked_at, checked_at, product_id))
                    if old_url.startswith("/media/products/") and old_url != image_url:
                        old_filename = old_url.rsplit("/", 1)[-1]
                        used = db.execute("SELECT 1 FROM products WHERE image_url=? LIMIT 1", (old_url,)).fetchone()
                        if not used and re.fullmatch(r"[a-f0-9]{20}\.(?:jpg|png|webp|gif)", old_filename):
                            try:
                                (PRODUCT_IMAGE_DIR / old_filename).unlink(missing_ok=True)
                            except OSError:
                                app.logger.warning("Could not delete replaced product image %s", old_filename)
                log_system_event("images", "image_link_updated", "Admin updated a product image link", 200, None, request.path, "info")
                flash("Product image updated.", "success")
            except ValueError as exc:
                flash(str(exc), "error")
            except sqlite3.Error:
                app.logger.exception("Could not update product image")
                flash("The image link could not be saved. Try again.", "error")
            return redirect(url_for("admin_images", status=request.args.get("status", "all"), q=request.args.get("q", ""), page=request.args.get("page", "1")))

    q = request.args.get("q", "").strip()[:100]
    status = request.args.get("status", "all")
    if status not in ("all", "with", "missing"):
        status = "all"
    try:
        page = max(1, min(100000, int(request.args.get("page", "1"))))
    except ValueError:
        page = 1
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).replace(microsecond=0).isoformat()
    where, params = [], []
    if q:
        like = f"%{q}%"
        where.append("(barcode LIKE ? OR name LIKE ? OR brand LIKE ?)")
        params.extend((like, like, like))
    if status == "with":
        where.append("COALESCE(TRIM(image_url),'')<>''")
    elif status == "missing":
        where.append("COALESCE(TRIM(image_url),'')=''")
    clause = " WHERE " + " AND ".join(where) if where else ""
    with connect_db() as db:
        totals = {
            "products": int(db.execute("SELECT COUNT(*) FROM products").fetchone()[0]),
            "with_images": int(db.execute("SELECT COUNT(*) FROM products WHERE COALESCE(TRIM(image_url),'')<>''").fetchone()[0]),
            "missing": int(db.execute("SELECT COUNT(*) FROM products WHERE COALESCE(TRIM(image_url),'')=''").fetchone()[0]),
            "unchecked": int(db.execute("SELECT COUNT(*) FROM products WHERE COALESCE(TRIM(image_url),'')='' AND (COALESCE(TRIM(image_checked_at),'')='' OR image_checked_at<?)", (cutoff,)).fetchone()[0]),
        }
        count = int(db.execute("SELECT COUNT(*) FROM products" + clause, params).fetchone()[0])
        per_page = 40
        total_pages = max(1, (count + per_page - 1) // per_page)
        page = min(page, total_pages)
        results = [dict(r) for r in db.execute("SELECT * FROM products" + clause + " ORDER BY CASE WHEN COALESCE(TRIM(image_url),'')='' THEN 0 ELSE 1 END, name COLLATE NOCASE LIMIT ? OFFSET ?",
                                               params + [per_page, (page - 1) * per_page])]
    return render_template("admin_images.html", results=results, totals=totals, q=q, status=status,
                           page=page, total_pages=total_pages, result_count=count)


@app.get(ADMIN_PREFIX + "/images/backup.zip")
@admin_required
def admin_images_backup():
    fd, filename = tempfile.mkstemp(prefix="barcode-hub-images-", suffix=".zip")
    os.close(fd)
    archive_path = Path(filename)
    try:
        with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            manifest = {"format": "kenya-barcode-hub-product-images", "created_at": now_iso(), "files": []}
            for image_file in PRODUCT_IMAGE_DIR.iterdir() if PRODUCT_IMAGE_DIR.exists() else []:
                if image_file.is_file() and re.fullmatch(r"[a-f0-9]{20}\.(?:jpg|png|webp|gif)", image_file.name):
                    archive.write(image_file, arcname="product-images/" + image_file.name)
                    manifest["files"].append(image_file.name)
            archive.writestr("manifest.json", json.dumps(manifest, separators=(",", ":")))
        response = send_file(archive_path, mimetype="application/zip", as_attachment=True,
                             download_name=f"barcode-hub-product-images-{datetime.now(timezone.utc).date().isoformat()}.zip", max_age=0)
        response.headers["Cache-Control"] = "no-store"
        response.call_on_close(lambda: archive_path.unlink(missing_ok=True))
        return response
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise


@app.route(ADMIN_PREFIX + "/users")
@admin_required
def admin_users():
    q = request.args.get("q", "").strip()[:120]
    try:
        page = max(1, min(100000, int(request.args.get("page", "1"))))
    except ValueError:
        page = 1
    where, params = "", []
    if q:
        like = f"%{q}%"
        where = "WHERE u.name LIKE ? OR u.email LIKE ? OR CAST(u.id AS TEXT)=?"
        params = [like, like, q]
    with connect_db() as db:
        total = int(db.execute("SELECT COUNT(*) FROM users u " + where, params).fetchone()[0])
        per_page = 50
        pages = max(1, (total + per_page - 1) // per_page)
        page = min(page, pages)
        now = now_iso()
        users = [dict(r) for r in db.execute(f"""SELECT u.id,u.name,u.email,u.created_at,
            (SELECT p.name FROM subscriptions s JOIN plans p ON p.id=s.plan_id
             WHERE s.user_id=u.id AND s.starts_at<=? AND s.expires_at>? ORDER BY s.expires_at DESC LIMIT 1) AS active_plan,
            (SELECT s.expires_at FROM subscriptions s WHERE s.user_id=u.id AND s.starts_at<=? AND s.expires_at>? ORDER BY s.expires_at DESC LIMIT 1) AS plan_expires,
            (SELECT COUNT(*) FROM api_keys k WHERE k.user_id=u.id AND k.revoked=0) AS active_keys,
            (SELECT MAX(created_at) FROM system_events e WHERE e.user_id=u.id AND e.event_type='user_login_succeeded') AS last_login
            FROM users u {where} ORDER BY u.id DESC LIMIT ? OFFSET ?""",
            [now, now, now, now] + params + [per_page, (page-1)*per_page])]
    return render_template("admin_users.html", users=users, q=q, page=page, total_pages=pages, total=total)


@app.route(ADMIN_PREFIX + "/users/<int:user_id>", methods=["GET", "POST"])
@admin_required
def admin_user_detail(user_id):
    temporary_password = None
    new_api_key = None
    if request.method == "POST":
        action = request.form.get("action", "")
        with connect_db() as db:
            user_row = db.execute("SELECT id,name,email FROM users WHERE id=?", (user_id,)).fetchone()
        if not user_row:
            abort(404)
        if action == "reset_password":
            temporary_password = secrets.token_urlsafe(15)
            changed_at = datetime.now(timezone.utc).isoformat()
            with connect_db() as db:
                db.execute("UPDATE users SET password_hash=?,password_changed_at=? WHERE id=?",
                           (generate_password_hash(temporary_password), changed_at, user_id))
            log_system_event("admin_support", "customer_password_reset", "Admin issued a temporary password shown once",
                             200, user_id, request.path, "warning")
        elif action == "issue_api_key":
            label = request.form.get("label", "Admin-issued recovery key").strip()[:80] or "Admin-issued recovery key"
            new_api_key = "kbh_" + secrets.token_urlsafe(32)
            digest = hashlib.sha256(new_api_key.encode()).hexdigest()
            with connect_db() as db:
                db.execute("INSERT INTO api_keys(user_id,label,prefix,token_hash,created_at) VALUES(?,?,?,?,?)",
                           (user_id, label, new_api_key[:12], digest, now_iso()))
            log_system_event("admin_support", "admin_issued_api_key", "Admin issued a replacement API key; secret shown once",
                             200, user_id, request.path, "warning")
        elif action == "revoke_keys":
            with connect_db() as db:
                cursor = db.execute("UPDATE api_keys SET revoked=1 WHERE user_id=? AND revoked=0", (user_id,))
                revoked_count = cursor.rowcount
            log_system_event("admin_support", "admin_revoked_api_keys", f"Admin revoked {revoked_count} API keys",
                             200, user_id, request.path, "warning")
            flash(f"Revoked {revoked_count} active API keys.", "success")
        elif action == "update_profile":
            name = request.form.get("name", "").strip()[:100]
            email = request.form.get("email", "").strip().lower()[:254]
            if len(name) < 2 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
                flash("Enter a valid name and email address.", "error")
            else:
                try:
                    with connect_db() as db:
                        db.execute("UPDATE users SET name=?,email=? WHERE id=?", (name, email, user_id))
                    log_system_event("admin_support", "admin_updated_profile", "Admin updated a customer profile",
                                     200, user_id, request.path, "warning")
                    flash("Customer account details updated.", "success")
                except sqlite3.IntegrityError:
                    flash("Another account already uses that email address.", "error")
        else:
            flash("Unknown support action.", "error")

    with connect_db() as db:
        user = db.execute("SELECT id,name,email,created_at FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            abort(404)
        user = dict(user)
        now = now_iso()
        sub = db.execute("""SELECT s.*,p.name AS plan_name,p.lookup_limit,p.image_access,p.price_kes
                           FROM subscriptions s JOIN plans p ON p.id=s.plan_id
                           WHERE s.user_id=? AND s.starts_at<=? AND s.expires_at>?
                           ORDER BY s.expires_at DESC LIMIT 1""", (user_id, now, now)).fetchone()
        active_plan = dict(sub) if sub else None
        keys = [dict(r) for r in db.execute("SELECT id,label,prefix,created_at,revoked FROM api_keys WHERE user_id=? ORDER BY id DESC", (user_id,))]
        orders = [dict(r) for r in db.execute("""SELECT o.*,p.name AS plan_name FROM orders o JOIN plans p ON p.id=o.plan_id
                                               WHERE o.user_id=? ORDER BY o.id DESC LIMIT 30""", (user_id,))]
        api_logs = [dict(r) for r in db.execute("SELECT barcode,status_code,created_at FROM api_logs WHERE user_id=? ORDER BY id DESC LIMIT 30", (user_id,))]
        events = [dict(r) for r in db.execute("SELECT event_type,severity,source,message,status_code,path,created_at FROM system_events WHERE user_id=? ORDER BY id DESC LIMIT 40", (user_id,))]
    response = make_response(render_template("admin_user_detail.html", user=user, subscription=active_plan,
                                             keys=keys, orders=orders, api_logs=api_logs, events=events,
                                             temporary_password=temporary_password, new_api_key=new_api_key))
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@app.route(ADMIN_PREFIX + "/api-monitor", methods=["GET", "POST"])
@admin_required
def admin_api_monitor():
    if request.method == "POST":
        try:
            with connect_db() as db:
                integrity = db.execute("PRAGMA quick_check").fetchone()[0]
                products = int(db.execute("SELECT COUNT(*) FROM products").fetchone()[0])
                images = int(db.execute("SELECT COUNT(*) FROM products WHERE COALESCE(TRIM(image_url),'')<>''").fetchone()[0])
                api_status = get_settings(db).get("api_enabled", "1") == "1"
            if integrity != "ok":
                raise sqlite3.DatabaseError("SQLite integrity check failed")
            log_system_event("system", "health_check_passed", "Admin health check passed: database and catalogue are readable",
                             200, None, request.path, "info")
            flash(f"Health check passed. Database OK; {products:,} products; {images:,} linked images; API {'enabled' if api_status else 'disabled by settings'}.", "success")
        except Exception:
            app.logger.exception("Admin-triggered health check failed")
            log_system_event("system", "health_check_failed", "Admin health check could not validate the database",
                             503, None, request.path, "error")
            flash("Health check failed. Review the hosting logs and database connection.", "error")
        return redirect(url_for("admin_api_monitor"))

    since = (datetime.now(timezone.utc) - timedelta(hours=24)).replace(microsecond=0).isoformat()
    with connect_db() as db:
        settings = get_settings(db)
        totals = {
            "calls": int(db.execute("SELECT COUNT(*) FROM system_events WHERE source='api' AND created_at>=?", (since,)).fetchone()[0]),
            "errors": int(db.execute("SELECT COUNT(*) FROM system_events WHERE source='api' AND status_code>=400 AND created_at>=?", (since,)).fetchone()[0]),
            "server_errors": int(db.execute("SELECT COUNT(*) FROM system_events WHERE source='api' AND status_code>=500 AND created_at>=?", (since,)).fetchone()[0]),
            "not_found": int(db.execute("SELECT COUNT(*) FROM system_events WHERE source='api' AND status_code=404 AND created_at>=?", (since,)).fetchone()[0]),
            "auth_errors": int(db.execute("SELECT COUNT(*) FROM system_events WHERE source='api' AND status_code=401 AND created_at>=?", (since,)).fetchone()[0]),
            "payment_errors": int(db.execute("SELECT COUNT(*) FROM system_events WHERE source='api' AND status_code=402 AND created_at>=?", (since,)).fetchone()[0]),
            "limits": int(db.execute("SELECT COUNT(*) FROM system_events WHERE source='api' AND status_code=429 AND created_at>=?", (since,)).fetchone()[0]),
            "login_failures": int(db.execute("SELECT COUNT(*) FROM system_events WHERE source='authentication' AND event_type='user_login_failed' AND created_at>=?", (since,)).fetchone()[0]),
            "images": int(db.execute("SELECT COUNT(*) FROM products WHERE COALESCE(TRIM(image_url),'')<>''").fetchone()[0]),
            "products": int(db.execute("SELECT COUNT(*) FROM products").fetchone()[0]),
        }
        events = [dict(r) for r in db.execute("""SELECT e.*,u.name AS user_name,u.email AS user_email
            FROM system_events e LEFT JOIN users u ON u.id=e.user_id ORDER BY e.id DESC LIMIT 120""")]
    return render_template("admin_api_monitor.html", totals=totals, events=events,
                           api_enabled=settings.get("api_enabled", "1") == "1", since=since)


@app.route(ADMIN_PREFIX + "/posts", methods=["GET", "POST"])
@admin_required
def admin_posts():
    if request.method == "POST":
        action = request.form.get("action", "add")
        if action == "delete":
            try:
                post_id = int(request.form.get("post_id", "0"))
            except ValueError:
                post_id = 0
            with connect_db() as db:
                db.execute("DELETE FROM posts WHERE id=?", (post_id,))
            flash("Post removed.", "success")
            return redirect(url_for("admin_posts"))
        title = request.form.get("title", "").strip()[:160]
        summary = request.form.get("summary", "").strip()[:600]
        url = request.form.get("url", "").strip()[:500]
        kind = request.form.get("kind", "link")
        premium = 1 if request.form.get("premium") == "on" else 0
        active = 1 if request.form.get("active") == "on" else 0
        if not title:
            flash("A title is required.", "error")
        elif url and not is_safe_external_url(url):
            flash("Links must start with https:// or http://.", "error")
        else:
            with connect_db() as db:
                if action == "edit":
                    try:
                        post_id = int(request.form.get("post_id", "0"))
                    except ValueError:
                        post_id = 0
                    db.execute("UPDATE posts SET title=?,summary=?,url=?,kind=?,premium=?,active=? WHERE id=?",
                               (title, summary, url, kind, premium, active, post_id))
                else:
                    db.execute("INSERT INTO posts(title,summary,url,kind,premium,active,created_at) VALUES(?,?,?,?,?,?,?)",
                               (title, summary, url, kind, premium, active, now_iso()))
            flash("Post saved.", "success")
        return redirect(url_for("admin_posts"))
    with connect_db() as db:
        posts = [dict(r) for r in db.execute("SELECT * FROM posts ORDER BY id DESC")]
    edit_id = request.args.get("edit", type=int)
    editing = next((p for p in posts if p["id"] == edit_id), None)
    return render_template("admin_posts.html", posts=posts, editing=editing)


@app.route(ADMIN_PREFIX + "/orders")
@admin_required
def admin_orders():
    status = request.args.get("status", "all")
    with connect_db() as db:
        if status in ("pending", "approved", "rejected"):
            rows = db.execute("""SELECT o.*,u.name AS user_name,u.email,u.created_at AS user_created,p.name AS plan_name
                               FROM orders o JOIN users u ON u.id=o.user_id JOIN plans p ON p.id=o.plan_id
                               WHERE o.status=? ORDER BY o.id DESC LIMIT 300""", (status,)).fetchall()
        else:
            rows = db.execute("""SELECT o.*,u.name AS user_name,u.email,u.created_at AS user_created,p.name AS plan_name
                               FROM orders o JOIN users u ON u.id=o.user_id JOIN plans p ON p.id=o.plan_id
                               ORDER BY o.id DESC LIMIT 300""").fetchall()
    return render_template("admin_orders.html", orders=[dict(r) for r in rows], status=status)


@app.post(ADMIN_PREFIX + "/orders/<int:order_id>/review")
@admin_required
def admin_review_order(order_id):
    action = request.form.get("action", "")
    note = request.form.get("note", "").strip()[:500]
    if action not in ("approve", "reject"):
        abort(400)
    with connect_db() as db:
        order = db.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not order:
            abort(404)
        if order["status"] != "pending":
            flash("This order was already reviewed.", "info")
            return redirect(url_for("admin_orders"))
        if action == "approve":
            plan = db.execute("SELECT * FROM plans WHERE id=?", (order["plan_id"],)).fetchone()
            if not plan:
                abort(400)
            current = db.execute("SELECT MAX(expires_at) AS e FROM subscriptions WHERE user_id=? AND expires_at>?",
                                 (order["user_id"], now_iso())).fetchone()["e"]
            try:
                start_dt = datetime.fromisoformat(current) if current else datetime.now(timezone.utc)
            except ValueError:
                start_dt = datetime.now(timezone.utc)
            start_dt = max(start_dt, datetime.now(timezone.utc))
            expires = (start_dt + timedelta(days=int(plan["duration_days"]))).replace(microsecond=0).isoformat()
            start_iso = start_dt.replace(microsecond=0).isoformat()
            db.execute("INSERT INTO subscriptions(user_id,plan_id,starts_at,expires_at,order_id) VALUES(?,?,?,?,?)",
                       (order["user_id"], order["plan_id"], start_iso, expires, order_id))
            db.execute("UPDATE orders SET status='approved',admin_note=?,reviewed_at=? WHERE id=?",
                       (note, now_iso(), order_id))
            flash("Payment approved and access activated.", "success")
        else:
            db.execute("UPDATE orders SET status='rejected',admin_note=?,reviewed_at=? WHERE id=?",
                       (note, now_iso(), order_id))
            flash("Payment marked as rejected.", "info")
    return redirect(url_for("admin_orders"))


@app.route(ADMIN_PREFIX + "/visitors")
@admin_required
def admin_visitors():
    q = request.args.get("q", "")[:100]
    page = max(1, request.args.get("page", 1, type=int))
    per_page = 100
    like = f"%{q}%"
    with connect_db() as db:
        total = db.execute("SELECT COUNT(*) FROM visitor_events").fetchone()[0]
        unique = db.execute("SELECT COUNT(DISTINCT visitor_id) FROM visitor_events").fetchone()[0]
        if q:
            matched = db.execute("SELECT COUNT(*) FROM visitor_events WHERE path LIKE ? OR referrer LIKE ? OR visitor_id LIKE ?",
                                 (like, like, like)).fetchone()[0]
            rows = db.execute("SELECT * FROM visitor_events WHERE path LIKE ? OR referrer LIKE ? OR visitor_id LIKE ? ORDER BY id DESC LIMIT ? OFFSET ?",
                              (like, like, like, per_page, (page - 1) * per_page)).fetchall()
        else:
            matched = total
            rows = db.execute("SELECT * FROM visitor_events ORDER BY id DESC LIMIT ? OFFSET ?", (per_page, (page - 1) * per_page)).fetchall()
        top_pages = [dict(r) for r in db.execute("SELECT path,COUNT(*) AS views,COUNT(DISTINCT visitor_id) AS visitors FROM visitor_events GROUP BY path ORDER BY views DESC LIMIT 10")]
    pages = max(1, (matched + per_page - 1) // per_page)
    return render_template("admin_visitors.html", events=[dict(r) for r in rows], total=total, unique=unique,
                           matched=matched, top_pages=top_pages, q=q, page=page, pages=pages)


@app.route(ADMIN_PREFIX + "/visitors.csv")
@admin_required
def admin_visitors_csv():
    with connect_db() as db:
        rows = db.execute("SELECT created_at,visitor_id,path,method,ip_masked,referrer,user_agent FROM visitor_events ORDER BY id DESC").fetchall()
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["visited_at_utc", "visitor_session_id", "path", "method", "masked_network", "referrer", "browser_user_agent"])
    writer.writerows([[safe_csv_value(v) for v in row] for row in rows])
    response = make_response("\ufeff" + output.getvalue())
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    response.headers["Content-Disposition"] = 'attachment; filename="barcode-hub-visitors.csv"'
    response.headers["Cache-Control"] = "no-store"
    return response


@app.route(ADMIN_PREFIX + "/settings", methods=["GET", "POST"])
@admin_required
def admin_settings():
    if request.method == "POST":
        editable = list(DEFAULT_SETTINGS.keys())
        values = {key: request.form.get(key, DEFAULT_SETTINGS[key]).strip()[:1000] for key in editable}
        # Restrict customization inputs to sensible values.
        color = values["primary_color"]
        if color and not (color.startswith("#") and len(color) in (4, 7) and all(ch in "0123456789abcdefABCDEF" for ch in color[1:])):
            values["primary_color"] = DEFAULT_SETTINGS["primary_color"]
        if values["logo_url"] and not is_safe_external_url(values["logo_url"]):
            values["logo_url"] = ""
        values["api_enabled"] = "1" if request.form.get("api_enabled") == "on" else "0"
        try:
            values["public_search_limit"] = str(max(1, min(10, int(values["public_search_limit"]))))
        except ValueError:
            values["public_search_limit"] = "5"
        with connect_db() as db:
            for key, value in values.items():
                db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        flash("Website settings saved.", "success")
        return redirect(url_for("admin_settings"))
    return render_template("admin_settings.html", settings=get_settings())


@app.route(ADMIN_PREFIX + "/plans", methods=["GET", "POST"])
@admin_required
def admin_plans():
    if request.method == "POST":
        try:
            plan_id = int(request.form.get("plan_id", "0"))
            name = request.form.get("name", "").strip()[:80]
            price = int(request.form.get("price_kes", "0"))
            limit = int(request.form.get("lookup_limit", "0"))
            days = int(request.form.get("duration_days", "30"))
            description = request.form.get("description", "").strip()[:200]
            active = 1 if request.form.get("active") == "on" else 0
            image_access = 1 if request.form.get("image_access") == "on" else 0
            if not name or price < 0 or limit < 1 or days < 1 or days > 365 or (active and image_access and price < 1):
                raise ValueError
        except ValueError:
            flash("Enter a valid plan name, price, lookup limit and duration (1–365 days). An active image plan must cost at least KSh 1.", "error")
            return redirect(url_for("admin_plans"))
        with connect_db() as db:
            db.execute("UPDATE plans SET name=?,price_kes=?,lookup_limit=?,duration_days=?,description=?,active=?,image_access=? WHERE id=?",
                       (name, price, limit, days, description, active, image_access, plan_id))
        flash("Plan updated.", "success")
        return redirect(url_for("admin_plans"))
    with connect_db() as db:
        plans = [dict(r) for r in db.execute("SELECT * FROM plans ORDER BY price_kes")]
    return render_template("admin_plans.html", plans=plans)


@app.errorhandler(400)
def bad_request(error):
    return render_template("error.html", code=400, message=getattr(error, "description", "The request could not be processed.")), 400


@app.errorhandler(404)
def not_found(error):
    return render_template("error.html", code=404, message="That page could not be found."), 404


@app.errorhandler(413)
def too_large(error):
    return render_template("error.html", code=413, message="That upload is too large. The maximum backup or catalogue upload is 128 MB."), 413


@app.errorhandler(500)
def server_error(error):
    app.logger.exception("Unhandled application error", exc_info=error)
    return render_template("error.html", code=500, message="Something went wrong. The error was logged on the server."), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=False)
