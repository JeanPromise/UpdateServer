from flask import Flask, request, jsonify, send_from_directory, session, redirect, url_for, render_template
import os
import time
import hashlib

app = Flask(__name__)

# ----------------- Config -----------------
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

SECRET_KEY = "super-secret-key"   # ⚠️ change before production
app.secret_key = SECRET_KEY
API_KEY = "290"

users = {}  # in-memory (replace with DB later)
LATEST_VERSION = "1.0.0"

# ----------------- Utils -----------------
def hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()

# ----------------- Static Landing -----------------
@app.route('/')
def home():
    return render_template("index.html")

# ----------------- Upload Endpoint -----------------
@app.route('/upload', methods=['POST'])
def upload_file():
    key = request.headers.get("X-API-KEY")
    if key != API_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    if 'apk' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['apk']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    timestamp = int(time.time())
    filename = f"app-{timestamp}.apk"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)

    # keep only latest
    for f in os.listdir(UPLOAD_FOLDER):
        if f != filename:
            os.remove(os.path.join(UPLOAD_FOLDER, f))

    global LATEST_VERSION
    LATEST_VERSION = f"1.{timestamp}"  # example bump
    return jsonify({
        "message": "File uploaded",
        "url": f"/uploads/{filename}",
        "version": LATEST_VERSION
    })

# ----------------- Serve APK -----------------
@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

# ----------------- Version Check -----------------
@app.route('/version')
def version_check():
    files = os.listdir(UPLOAD_FOLDER)
    if not files:
        return jsonify({"latest_version": None, "download_url": None})
    latest = max(files, key=lambda f: os.path.getctime(os.path.join(UPLOAD_FOLDER, f)))
    return jsonify({
        "latest_version": LATEST_VERSION,
        "download_url": url_for('uploaded_file', filename=latest, _external=True)
    })

# ----------------- Auth -----------------
@app.route('/login', methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if username in users and users[username] == hash_password(password):
            session["user"] = username
            return redirect(url_for("hub"))
        return "❌ Invalid login", 401
    return render_template("login.html")

@app.route('/register', methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        if username in users:
            return "⚠️ User already exists", 400
        users[username] = hash_password(password)
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route('/logout')
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))

# ----------------- Hub -----------------
@app.route('/hub')
def hub():
    if "user" not in session:
        return redirect(url_for("login"))
    return render_template("hub.html", user=session["user"])

# ----------------- Watch + Sidebar -----------------
@app.route('/watch')
def watch():
    if "user" not in session:
        return redirect(url_for("login"))
    return render_template("watch.html")

@app.route('/downloads')
def downloads():
    if "user" not in session:
        return redirect(url_for("login"))
    return "<h2 style='color:white;background:black;text-align:center;'>📂 Downloads Page</h2>"

@app.route('/history')
def history():
    if "user" not in session:
        return redirect(url_for("login"))
    return "<h2 style='color:white;background:black;text-align:center;'>⏱ Last Watched Page</h2>"

@app.route('/delete')
def delete():
    if "user" not in session:
        return redirect(url_for("login"))
    return "<h2 style='color:white;background:black;text-align:center;'>🗑 Delete Page</h2>"

# ----------------- Run -----------------
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
