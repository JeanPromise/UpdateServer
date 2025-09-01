import os
from flask import Flask, request, jsonify, send_from_directory
from flask_socketio import SocketIO, emit

# ==== Config ====
UPLOAD_FOLDER = 'uploads'
if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

CURRENT_VERSION_FILE = os.path.join(UPLOAD_FOLDER, 'current_version.txt')
CURRENT_FILE_NAME = os.path.join(UPLOAD_FOLDER, 'current_file.txt')

app = Flask(__name__)
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='gevent')

connected_devices = {}

# ==== Helpers ====
def get_current_version():
    if os.path.exists(CURRENT_VERSION_FILE):
        with open(CURRENT_VERSION_FILE, 'r') as f:
            return f.read().strip()
    return "0"

def set_current_version(version):
    with open(CURRENT_VERSION_FILE, 'w') as f:
        f.write(version)

def get_current_file():
    if os.path.exists(CURRENT_FILE_NAME):
        with open(CURRENT_FILE_NAME, 'r') as f:
            return f.read().strip()
    return None

def set_current_file(filename):
    with open(CURRENT_FILE_NAME, 'w') as f:
        f.write(filename)

# ==== Routes ====
@app.route('/')
def index():
    return "Update Server is Running"

@app.route('/upload', methods=['POST'])
def upload_apk():
    if 'file' not in request.files:
        return jsonify({"status": "error", "message": "No file part"}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({"status": "error", "message": "No selected file"}), 400
    filename = file.filename
    version = request.form.get('version', '1')
    file_path = os.path.join(UPLOAD_FOLDER, filename)
    file.save(file_path)
    set_current_version(version)
    set_current_file(filename)
    socketio.emit('apk_update', {'version': version, 'filename': filename})
    print(f"[{socketio.server.manager.clock()}] APK Updated: v{version} ({filename})")
    return jsonify({"status": "success", "filename": filename, "version": version})

@app.route('/download')
def download_apk():
    current_file = get_current_file()
    if current_file:
        return send_from_directory(UPLOAD_FOLDER, current_file, as_attachment=True)
    return jsonify({"status": "error", "message": "No APK available"}), 404

@app.route('/devices', methods=['GET'])
def get_devices():
    return jsonify(connected_devices)

# ==== WebSocket events ====
@socketio.on('connect')
def handle_connect():
    device_id = request.args.get('device_id', 'unknown')
    connected_devices[device_id] = get_current_version()
    emit('connected', {'message': f'Connected as {device_id}'})
    print(f"[{socketio.server.manager.clock()}] Device connected: {device_id}")

@socketio.on('disconnect')
def handle_disconnect():
    device_id = request.args.get('device_id', 'unknown')
    connected_devices.pop(device_id, None)
    print(f"[{socketio.server.manager.clock()}] Device disconnected: {device_id}")

# ==== Main ====
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
