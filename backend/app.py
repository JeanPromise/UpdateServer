from flask import Flask, render_template, request, send_from_directory
from flask_socketio import SocketIO, emit
import os

app = Flask(__name__, static_folder='admin-ui', template_folder='admin-ui')
socketio = SocketIO(app, cors_allowed_origins="*")

# Keep track of connected devices
connected_devices = {}

@app.route('/')
def index():
    return send_from_directory('admin-ui', 'index.html')

@app.route('/<path:path>')
def static_proxy(path):
    return send_from_directory('admin-ui', path)

# WebSocket events
@socketio.on('connect')
def on_connect():
    print("[+] Device connected")
    emit('admin_message', {'message': 'Connected to server'})

@socketio.on('device_info')
def handle_device_info(data):
    device_id = data.get('device_id')
    version = data.get('current_version')
    if device_id:
        connected_devices[device_id] = {'version': version}
        print(f"[+] Device registered: {device_id} (version {version})")
        emit('update_devices', connected_devices, broadcast=True)

@socketio.on('disconnect')
def on_disconnect():
    print("[-] Device disconnected")
    # Optionally remove device

# Admin triggers APK update
@app.route('/update', methods=['POST'])
def trigger_update():
    apk_url = request.form.get('apk_url')
    if not apk_url:
        return {'status': 'error', 'message': 'No APK URL provided'}, 400
    print(f"[+] Sending update command to devices: {apk_url}")
    socketio.emit('update_apk', {'url': apk_url})
    return {'status': 'success', 'message': 'Update command sent'}

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)
