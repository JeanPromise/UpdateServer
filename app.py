from flask import Flask, request, jsonify, send_from_directory
import os, json

app = Flask(__name__, static_url_path='', static_folder='.')

USERS_FILE = 'users.json'
APK_FILE = 'apk.json'

# ------------------ Helper Functions ------------------
def read_json(file_path, default):
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except Exception:
        return default

def write_json(file_path, data):
    try:
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        print(f"Error writing {file_path}: {e}")

# Ensure files exist
if not os.path.exists(USERS_FILE):
    write_json(USERS_FILE, [])

if not os.path.exists(APK_FILE):
    write_json(APK_FILE, {"version": None, "filename": None})

# ----------------------- INDEX ENDPOINTS -----------------------
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


@app.route('/check_update')
def check_update():
    apk = read_json(APK_FILE, {"version": None, "filename": None})
    update_required = apk['version'] is not None
    return jsonify({"update_required": update_required, "apk_version": apk['version']})


@app.route('/register', methods=['POST'])
def register():
    data = request.get_json() or {}
    name = data.get('name')
    email = data.get('email')
    password = data.get('password')

    if not name or not email or not password:
        return jsonify({"success": False, "message": "All fields required."})

    users = read_json(USERS_FILE, [])

    if any(u['email'] == email for u in users):
        return jsonify({"success": False, "message": "Email already registered."})

    users.append({
        "name": name,
        "email": email,
        "password": password,
        "enabled": True
    })

    write_json(USERS_FILE, users)
    return jsonify({"success": True})


@app.route('/login', methods=['POST'])
def login():
    data = request.get_json() or {}
    email = data.get('email')
    password = data.get('password')

    if not email or not password:
        return jsonify({"success": False, "message": "Email and password required."})

    users = read_json(USERS_FILE, [])
    for user in users:
        if user['email'] == email:
            if not user.get('enabled', True):
                return jsonify({"success": False, "message": "User is disabled."})
            if user['password'] == password:
                return jsonify({"success": True})
            else:
                return jsonify({"success": False, "message": "Incorrect password."})
    return jsonify({"success": False, "message": "Email not registered."})


# ----------------------- ADMIN ENDPOINTS -----------------------
@app.route('/admin')
def admin_dashboard():
    return send_from_directory('.', 'admin.html')


@app.route('/upload_apk', methods=['POST'])
def upload_apk():
    if 'apk' not in request.files or 'version' not in request.form:
        return jsonify({"success": False, "message": "APK file and version required."})

    apk_file = request.files['apk']
    version = request.form['version']
    filename = apk_file.filename

    # Save APK to root
    apk_file.save(filename)

    # Update apk.json
    write_json(APK_FILE, {"version": version, "filename": filename})

    return jsonify({"success": True, "message": "APK uploaded."})


@app.route('/download_apk')
def download_apk():
    apk = read_json(APK_FILE, {"filename": None})
    if not apk.get("filename") or not os.path.exists(apk["filename"]):
        return jsonify({"success": False, "message": "APK not available."}), 404
    return send_from_directory('.', apk["filename"], as_attachment=True)


@app.route('/get_users')
def get_users():
    users = read_json(USERS_FILE, [])
    return jsonify(users)


@app.route('/toggle_user', methods=['POST'])
def toggle_user():
    data = request.get_json() or {}
    email = data.get('email')
    enable = data.get('enable', True)

    users = read_json(USERS_FILE, [])

    for user in users:
        if user['email'] == email:
            user['enabled'] = enable
            break

    write_json(USERS_FILE, users)
    return jsonify({"success": True})


@app.route('/enable_all', methods=['POST'])
def enable_all():
    users = read_json(USERS_FILE, [])
    for user in users:
        user['enabled'] = True
    write_json(USERS_FILE, users)
    return jsonify({"success": True})


@app.route('/disable_all', methods=['POST'])
def disable_all():
    users = read_json(USERS_FILE, [])
    for user in users:
        user['enabled'] = False
    write_json(USERS_FILE, users)
    return jsonify({"success": True})


if __name__ == '__main__':
    # Debug True only for local testing; use gunicorn in production
    app.run(debug=True, port=5000, threaded=True)
