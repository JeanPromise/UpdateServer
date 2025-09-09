from flask import Flask, request, jsonify, send_from_directory
import os, json
from werkzeug.utils import secure_filename

app = Flask(__name__, static_url_path='', static_folder='.')

USERS_FILE = 'users.json'
APK_FILE = 'apk.json'

# ------------------- ENSURE FILES EXIST -------------------
if not os.path.exists(USERS_FILE):
    with open(USERS_FILE, 'w') as f:
        json.dump([], f)

if not os.path.exists(APK_FILE):
    with open(APK_FILE, 'w') as f:
        json.dump({"version": None, "filename": None}, f)


# ------------------- INDEX ENDPOINTS -------------------
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


@app.route('/check_update')
def check_update():
    with open(APK_FILE, 'r') as f:
        apk = json.load(f)
    update_required = apk['version'] is not None
    return jsonify({"update_required": update_required, "apk_version": apk['version']})


@app.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    name = data.get('name')
    email = data.get('email')
    password = data.get('password')

    with open(USERS_FILE, 'r') as f:
        users = json.load(f)

    if any(u['email'] == email for u in users):
        return jsonify({"success": False, "message": "Email already registered."})

    users.append({
        "name": name,
        "email": email,
        "password": password,
        "enabled": True
    })

    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=2)

    return jsonify({"success": True})


@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    email = data.get('email')
    password = data.get('password')

    with open(USERS_FILE, 'r') as f:
        users = json.load(f)

    for user in users:
        if user['email'] == email:
            if not user.get('enabled', True):
                return jsonify({"success": False, "message": "User is disabled."})
            if user['password'] == password:
                return jsonify({"success": True})
            else:
                return jsonify({"success": False, "message": "Incorrect password."})
    return jsonify({"success": False, "message": "Email not registered."})


# ------------------- APK ENDPOINTS -------------------
@app.route('/download_apk')
def download_apk():
    with open(APK_FILE, 'r') as f:
        apk = json.load(f)

    if not apk.get("filename") or not os.path.exists(apk["filename"]):
        return jsonify({"success": False, "message": "No APK available."}), 404

    return send_from_directory('.', apk["filename"], as_attachment=True)


# ------------------- ADMIN ENDPOINTS -------------------
@app.route('/admin')
def admin_dashboard():
    return send_from_directory('.', 'admin.html')


@app.route('/upload_apk', methods=['POST'])
def upload_apk():
    if 'apk' not in request.files or 'version' not in request.form:
        return jsonify({"success": False, "message": "APK file and version required."})

    apk_file = request.files['apk']
    version = request.form['version']
    filename = secure_filename(apk_file.filename)

    # Save APK to root folder
    apk_file.save(filename)

    # Update apk.json
    with open(APK_FILE, 'w') as f:
        json.dump({"version": version, "filename": filename}, f, indent=2)

    return jsonify({"success": True, "message": "APK uploaded."})


@app.route('/get_users')
def get_users():
    with open(USERS_FILE, 'r') as f:
        users = json.load(f)
    return jsonify(users)


@app.route('/toggle_user', methods=['POST'])
def toggle_user():
    data = request.get_json()
    email = data.get('email')
    enable = data.get('enable', True)

    with open(USERS_FILE, 'r') as f:
        users = json.load(f)

    for user in users:
        if user['email'] == email:
            user['enabled'] = enable
            break

    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=2)

    return jsonify({"success": True})


@app.route('/enable_all', methods=['POST'])
def enable_all():
    with open(USERS_FILE, 'r') as f:
        users = json.load(f)

    for user in users:
        user['enabled'] = True

    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=2)

    return jsonify({"success": True})


@app.route('/disable_all', methods=['POST'])
def disable_all():
    with open(USERS_FILE, 'r') as f:
        users = json.load(f)

    for user in users:
        user['enabled'] = False

    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=2)

    return jsonify({"success": True})


# ------------------- RUN -------------------
if __name__ == '__main__':
    app.run(debug=True, port=5000)
