from flask import Flask, send_from_directory, request
from flask_socketio import SocketIO, emit
import os
import requests

app = Flask(__name__, static_folder='admin-ui', static_url_path='/admin-ui')
socketio = SocketIO(app, cors_allowed_origins="*")

# ------------------------
# --- Static Files ---
# ------------------------
@app.route('/')
def index():
    return send_from_directory('admin-ui', 'index.html')

@app.route('/<path:path>')
def static_proxy(path):
    return send_from_directory('admin-ui', path)

# ------------------------
# --- Device Management ---
# ------------------------
connected_devices = {}

@socketio.on('connect')
def handle_connect():
    print(f"Device connected: {request.sid}")
    emit('server_message', {'message': 'Connected to admin server'})

@socketio.on('register_device')
def handle_register(data):
    device_id = data.get('device_id')
    version = data.get('current_version')
    if device_id:
        connected_devices[device_id] = {'sid': request.sid, 'version': version}
        print(f"Registered device: {device_id} version {version}")
        emit('server_message', {'message': 'Device registered'})

# ------------------------
# --- Admin Commands ---
# ------------------------
@socketio.on('send_command')
def handle_command(data):
    target_id = data.get('device_id')
    command = data.get('command')
    if target_id in connected_devices:
        sid = connected_devices[target_id]['sid']
        emit('command', command, room=sid)
        emit('server_message', {'message': f'Command sent to {target_id}'})
    else:
        emit('server_message', {'message': f'Device {target_id} not found'})

# ------------------------
# --- Optional API for updates ---
# ------------------------
@app.route('/update_apk', methods=['POST'])
def update_apk():
    apk_url = request.json.get('url')
    device_id = request.json.get('device_id')
    if device_id in connected_devices:
        sid = connected_devices[device_id]['sid']
        socketio.emit('command', {'action': 'update_apk', 'url': apk_url}, room=sid)
        return {'status': 'ok', 'message': f'Update sent to {device_id}'}
    return {'status': 'error', 'message': 'Device not found'}, 404

# ------------------------
# --- Run ---
# ------------------------
if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
