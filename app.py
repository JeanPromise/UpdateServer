from flask import Flask, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename
import os
import json
from datetime import datetime

app = Flask(__name__)
UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
USERS_FILE = 'users.json'

# Load or create users.json
if not os.path.exists(USERS_FILE):
    with open(USERS_FILE, 'w') as f:
        json.dump({}, f)

def load_users():
    with open(USERS_FILE, 'r') as f:
        return json.load(f)

def save_users(users):
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=2)

# -----------------------
# Routes
# -----------------------

@app.route('/')
def home():
    return send_from_directory('.', 'index.html')

# Upload APK
@app.route('/upload_apk', methods=['POST'])
def upload_apk():
    if 'apk' not in request.files:
        return jsonify({'status': 'error', 'message': 'No APK file'}), 400
    file = request.files['apk']
    if file.filename == '':
        return jsonify({'status': 'error', 'message': 'No selected file'}), 400
    filename = secure_filename(file.filename)
    
    # Auto-increment version number based on timestamp
    version = datetime.now().strftime("%Y.%m.%d.%H%M%S")
    saved_filename = f"{os.path.splitext(filename)[0]}_v{version}.apk"
    file.save(os.path.join(UPLOAD_FOLDER, saved_filename))
    
    apk_url = f"/downloads/{saved_filename}"
    return jsonify({'status': 'success', 'url': apk_url, 'version': version})

# Serve APK downloads
@app.route('/downloads/<path:filename>')
def download_apk(filename):
    return send_from_directory(UPLOAD_FOLDER, filename, as_attachment=True)

# Get all registered users
@app.route('/users')
def users():
    users = load_users()
    return jsonify(users)

# Track online users (simplified)
ONLINE = {}

@app.route('/online', methods=['POST'])
def online():
    data = request.json
    user = data.get('user')
    status = data.get('status', 'offline')
    ONLINE[user] = status
    return jsonify({'status': 'ok', 'online_users': ONLINE})

@app.route('/online')
def get_online():
    return jsonify(ONLINE)

# -----------------------
# Run
# -----------------------
if __name__ == '__main__':
    app.run(debug=True)
