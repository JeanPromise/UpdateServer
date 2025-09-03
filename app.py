from flask import Flask, request, jsonify, send_from_directory
import os
import time

app = Flask(__name__)

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# 🚨 Change this secret key to your own long random string!
SECRET_KEY = "290"

# ================= Upload Endpoint =================
@app.route('/upload', methods=['POST'])
def upload_file():
    # 🔑 API Key check
    key = request.headers.get("X-API-KEY")
    if key != SECRET_KEY:
        return jsonify({"error": "Unauthorized"}), 401

    # ✅ Validate file
    if 'apk' not in request.files:
        return jsonify({"error": "No file part"}), 400
    file = request.files['apk']
    if file.filename == '':
        return jsonify({"error": "No selected file"}), 400

    # 🕒 Save with timestamped name
    timestamp = int(time.time())
    filename = f"app-{timestamp}.apk"
    filepath = os.path.join(UPLOAD_FOLDER, filename)
    file.save(filepath)

    # 🧹 Delete old files, keep only latest
    for f in os.listdir(UPLOAD_FOLDER):
        if f != filename:
            os.remove(os.path.join(UPLOAD_FOLDER, f))

    url = f"/uploads/{filename}"
    return jsonify({"message": "File uploaded", "url": url})

# ================= Serve APKs =================
@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

# ================= Home Page =================
@app.route('/')
def index():
    files = os.listdir(UPLOAD_FOLDER)
    if not files:
        return """
        <html>
          <head>
            <title>Latest APK</title>
            <style>
              body { background-color: #141414; color: white; font-family: Arial, sans-serif; text-align: center; margin-top: 100px; }
              .msg { font-size: 22px; opacity: 0.8; }
            </style>
          </head>
          <body>
            <div class="msg">🚫 No APK uploaded yet.</div>
          </body>
        </html>
        """
    latest = max(files, key=lambda f: os.path.getctime(os.path.join(UPLOAD_FOLDER, f)))
    return f"""
    <html>
      <head>
        <title>Download Latest APK</title>
        <style>
          body {{ background-color: #141414; color: white; font-family: Arial, sans-serif; text-align: center; margin-top: 100px; }}
          h1 {{ font-size: 28px; margin-bottom: 30px; }}
          a.download {{
            display: inline-block;
            padding: 15px 30px;
            background: #e50914;
            color: white;
            font-size: 20px;
            font-weight: bold;
            text-decoration: none;
            border-radius: 6px;
            transition: background 0.3s;
          }}
          a.download:hover {{
            background: #f6121d;
          }}
        </style>
      </head>
      <body>
        <h1>📲 Download Latest APK</h1>
        <a class="download" href="/uploads/{latest}">Download Now</a>
      </body>
    </html>
    """

# ================= Run App =================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))  # Render sets PORT env var
    app.run(host="0.0.0.0", port=port, debug=False)
