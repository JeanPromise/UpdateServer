import os
from flask import Flask, send_from_directory, request, jsonify
from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename

# Correct folder name
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Match the actual folder name
app = Flask(__name__, static_folder='../admin_ui')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
socketio = SocketIO(app, cors_allowed_origins="*")

# Serve the admin UI index.html
@app.route('/')
def index():
    return send_from_directory('../admin_ui', 'index.html')

# Upload endpoint
@app.route('/upload', methods=['POST'])
def upload_apk():
    if 'apk' not in request.files:
        return jsonify({'error': 'No APK part'}), 400
    file = request.files['apk']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    filename = secure_filename(file.filename)
    path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
    file.save(path)
    socketio.emit('new_apk', {'filename': filename})
    return jsonify({'success': True, 'filename': filename})

# Serve APKs
@app.route('/apk/<filename>')
def get_apk(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# WebSocket events
@socketio.on('connect')
def on_connect():
    print(f'Client connected: {request.sid}')
    emit('status', {'msg': 'Connected to update server'})

@socketio.on('disconnect')
def on_disconnect():
    print(f'Client disconnected: {request.sid}')

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=10000, debug=True)
