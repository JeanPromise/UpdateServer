from flask import Flask, request, jsonify, send_from_directory
import os, json, shutil

app = Flask(__name__, static_url_path='', static_folder='.')

USERS_FILE = 'users.json'
APK_FILE = 'current.apk'
APK_VERSION_FILE = 'apk_version.json'

# ===== Helper functions =====
def read_users():
    if not os.path.exists(USERS_FILE):
        return []
    with open(USERS_FILE, 'r') as f:
        return json.load(f)

def write_users(users):
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f, indent=2)

def read_apk_version():
    if not os.path.exists(APK_VERSION_FILE):
        return None
    with open(APK_VERSION_FILE, 'r') as f:
        return json.load(f).get('version')

def write_apk_version(version):
    with open(APK_VERSION_FILE, 'w') as f:
        json.dump({"version":version}, f, indent=2)

# ===== Routes =====
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/admin')
def admin_page():
    return send_from_directory('.', 'admin.html')

# ===== User registration/login =====
@app.route('/register', methods=['POST'])
def register():
    data = request.json
    name = data.get('name')
    email = data.get('email')
    password = data.get('password')
    if not all([name,email,password]):
        return jsonify({"status":"error", "msg":"All fields required"}),400
    users = read_users()
    if any(u['email']==email for u in users):
        return jsonify({"status":"error", "msg":"Email already exists"}),400
    users.append({"name":name,"email":email,"password":password,"enabled":True})
    write_users(users)
    return jsonify({"status":"success"})

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email')
    password = data.get('password')
    users = read_users()
    for u in users:
        if u['email']==email and u['password']==password:
            if not u.get('enabled', True):
                return jsonify({"status":"error","msg":"User disabled"}),403
            return jsonify({"status":"success"})
    return jsonify({"status":"error","msg":"Invalid credentials"}),401

# ===== APK upload =====
@app.route('/upload_apk', methods=['POST'])
def upload_apk():
    if 'apk' not in request.files or 'version' not in request.form:
        return jsonify({"status":"error","msg":"APK file and version required"}),400
    apk_file = request.files['apk']
    version = request.form['version']
    apk_file.save(APK_FILE)
    write_apk_version(version)
    return jsonify({"status":"success","msg":"APK uploaded"})

# ===== APK info =====
@app.route('/apk_info')
def apk_info():
    version = read_apk_version()
    exists = os.path.exists(APK_FILE)
    return jsonify({"exists":exists,"version":version})

# ===== Admin user management =====
@app.route('/users')
def get_users():
    return jsonify(read_users())

@app.route('/toggle_user/<int:index>', methods=['POST'])
def toggle_user(index):
    users = read_users()
    if 0 <= index < len(users):
        users[index]['enabled'] = not users[index].get('enabled', True)
        write_users(users)
        return jsonify({"status":"success"})
    return jsonify({"status":"error"}),400

@app.route('/enable_all', methods=['POST'])
def enable_all():
    users = read_users()
    for u in users: u['enabled'] = True
    write_users(users)
    return jsonify({"status":"success"})

@app.route('/disable_all', methods=['POST'])
def disable_all():
    users = read_users()
    for u in users: u['enabled'] = False
    write_users(users)
    return jsonify({"status":"success"})

# ===== Serve APK download =====
@app.route('/download_apk')
def download_apk():
    if os.path.exists(APK_FILE):
        return send_from_directory('.', APK_FILE, as_attachment=True)
    return jsonify({"status":"error","msg":"APK not found"}),404

# ===== Run server =====
if __name__ == '__main__':
    app.run(debug=True, port=5000)
