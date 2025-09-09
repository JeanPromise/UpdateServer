from flask import Flask, request, send_from_directory, jsonify
import os
import json
from werkzeug.utils import secure_filename

app = Flask(__name__, static_url_path='', static_folder='.')

# Ensure folders and files exist
APK_FOLDER = 'apk'
USERS_FILE = 'users.json'
APK_JSON = 'latest_apk.json'
os.makedirs(APK_FOLDER, exist_ok=True)
if not os.path.exists(USERS_FILE):
    with open(USERS_FILE, 'w') as f:
        json.dump([], f)
if not os.path.exists(APK_JSON):
    with open(APK_JSON, 'w') as f:
        json.dump({"filename": "", "version": ""}, f)

# ===== Serve static pages =====
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/admin')
def admin_page():
    return send_from_directory('.', 'admin.html')

# ===== Upload APK =====
@app.route('/admin/upload', methods=['POST'])
def upload_apk():
    if 'apk' not in request.files:
        return jsonify({"success": False, "error": "No APK file"})
    apk_file = request.files['apk']
    version = request.form.get('version')
    if not version:
        return jsonify({"success": False, "error": "No version specified"})
    
    filename = secure_filename(apk_file.filename)
    save_path = os.path.join(APK_FOLDER, filename)
    
    # Save APK
    apk_file.save(save_path)
    
    # Update latest_apk.json
    with open(APK_JSON, 'w') as f:
        json.dump({"filename": filename, "version": version}, f)
    
    return jsonify({"success": True, "filename": filename, "version": version})

# ===== Check latest APK =====
@app.route('/apk/latest', methods=['GET'])
def latest_apk():
    with open(APK_JSON, 'r') as f:
        data = json.load(f)
    return jsonify(data)

# ===== Serve APK files =====
@app.route('/apk/<path:filename>')
def serve_apk(filename):
    return send_from_directory(APK_FOLDER, filename)

# ===== User registration/login =====
@app.route('/user/register', methods=['POST'])
def register_user():
    data = request.json
    name = data.get('name')
    email = data.get('email')
    password = data.get('password')
    if not all([name, email, password]):
        return jsonify({"success": False, "error": "Missing fields"})
    
    # Load users
    with open(USERS_FILE, 'r') as f:
        users = json.load(f)
    
    # Check existing email
    if any(u['email'] == email for u in users):
        return jsonify({"success": False, "error": "Email already registered"})
    
    # Save new user
    users.append({"name": name, "email": email, "password": password})
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f)
    
    return jsonify({"success": True})

@app.route('/user/login', methods=['POST'])
def login_user():
    data = request.json
    email = data.get('email')
    password = data.get('password')
    if not all([email, password]):
        return jsonify({"success": False, "error": "Missing fields"})
    
    with open(USERS_FILE, 'r') as f:
        users = json.load(f)
    
    user = next((u for u in users if u['email']==email and u['password']==password), None)
    if user:
        return jsonify({"success": True, "name": user['name']})
    else:
        return jsonify({"success": False, "error": "Invalid credentials"})

if __name__ == '__main__':
    app.run(debug=True, port=5000)
