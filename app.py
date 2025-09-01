import os
from flask import Flask, render_template, request, send_from_directory
from flask_socketio import SocketIO, emit

# Set template folder to the same folder as this file
app = Flask(__name__, template_folder=os.path.dirname(os.path.abspath(__file__)))
socketio = SocketIO(app)

# Keep track of APK versions and registered users
latest_apk = None
registered_users = []

@app.route('/')
def index():
    global latest_apk, registered_users
    return render_template("index.html", latest_apk=latest_apk, users=registered_users)

@app.route('/upload', methods=['POST'])
def upload_apk():
    global latest_apk
    if 'file' not in request.files:
        return "No file part", 400
    file = request.files['file']
    if file.filename == '':
        return "No selected file", 400
    filename = file.filename
    file.save(os.path.join(os.path.dirname(os.path.abspath(__file__)), filename))
    latest_apk = filename
    socketio.emit('apk_updated', {'apk': latest_apk})
    return f"APK {filename} uploaded successfully", 200

@app.route('/register', methods=['POST'])
def register_user():
    data = request.json
    user_id = data.get('user_id')
    if user_id and user_id not in registered_users:
        registered_users.append(user_id)
        socketio.emit('user_list_updated', {'users': registered_users})
    return {"status": "registered", "users": registered_users}

# Serve APK files directly
@app.route('/apk/<filename>')
def get_apk(filename):
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)), filename)

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=10000)
