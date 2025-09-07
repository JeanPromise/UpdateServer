from flask import Flask, request, jsonify, send_from_directory, session, redirect, url_for, render_template_string
import os
import time
import hashlib

app = Flask(__name__)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# 🚨 Change these before deploying!
SECRET_KEY = "super-secret-key"
app.secret_key = SECRET_KEY
API_KEY = "290"

# In-memory users (replace with DB later if needed)
users = {}

# Latest APK version (update this whenever you upload new)
LATEST_VERSION = "1.0.0"


# ================= Utils =================
def hash_password(pw: str) -> str:
    return hashlib.sha256(pw.encode()).hexdigest()


# ================= Upload Endpoint =================
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

    # Keep only latest
    for f in os.listdir(UPLOAD_FOLDER):
        if f != filename:
            os.remove(os.path.join(UPLOAD_FOLDER, f))

    global LATEST_VERSION
    LATEST_VERSION = f"1.{timestamp}"  # Example version bump
    url = f"/uploads/{filename}"
    return jsonify({"message": "File uploaded", "url": url, "version": LATEST_VERSION})


# ================= Serve APKs =================
@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)


# ================= Version Check API =================
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


# ================= Auth Pages =================
@app.route('/login', methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if username in users and users[username] == hash_password(password):
            session["user"] = username
            return redirect(url_for("hub"))
        return "❌ Invalid login", 401

    return render_template_string("""
    <html>
      <body style="background:#141414;color:white;font-family:sans-serif;text-align:center;">
        <h2>Login</h2>
        <form method="POST">
          <input type="text" name="username" placeholder="Username" required><br><br>
          <input type="password" name="password" placeholder="Password" required><br><br>
          <button type="submit">Login</button>
        </form>
        <p><a href="/register" style="color:#e50914;">Register</a></p>
      </body>
    </html>
    """)


@app.route('/register', methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if username in users:
            return "⚠️ User already exists", 400

        users[username] = hash_password(password)
        return redirect(url_for("login"))

    return render_template_string("""
    <html>
      <body style="background:#141414;color:white;font-family:sans-serif;text-align:center;">
        <h2>Register</h2>
        <form method="POST">
          <input type="text" name="username" placeholder="Username" required><br><br>
          <input type="password" name="password" placeholder="Password" required><br><br>
          <button type="submit">Register</button>
        </form>
        <p><a href="/login" style="color:#e50914;">Login</a></p>
      </body>
    </html>
    """)


# ================= Hub Page =================
@app.route('/hub')
def hub():
    if "user" not in session:
        return redirect(url_for("login"))

    return render_template_string("""
    <html>
      <body style="background:#141414;color:white;font-family:sans-serif;text-align:center;">
        <h2>🎬 Welcome {{ user }}</h2>
        <p><a href="/watch" style="color:#e50914;">Go to Watch</a></p>
        <p><a href="/logout" style="color:#888;">Logout</a></p>
      </body>
    </html>
    """, user=session["user"])


# ================= Watch + Sidebar =================
@app.route('/watch')
def watch():
    if "user" not in session:
        return redirect(url_for("login"))

    return render_template_string("""
    <html>
      <body style="background:#000;color:white;font-family:sans-serif;">
        <div style="display:flex;">
          <div style="width:200px;background:#111;padding:10px;">
            <h3>📂 Sidebar</h3>
            <ul>
              <li><a href="/downloads" style="color:#e50914;">Downloads</a></li>
              <li><a href="/history" style="color:#e50914;">Last Watched</a></li>
              <li><a href="/delete" style="color:#e50914;">Delete</a></li>
            </ul>
          </div>
          <div style="flex:1;text-align:center;">
            <h2>Watch Page</h2>
            <p>Here goes your video logic...</p>
          </div>
        </div>
      </body>
    </html>
    """)


@app.route('/logout')
def logout():
    session.pop("user", None)
    return redirect(url_for("login"))


# ================= Run App =================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
