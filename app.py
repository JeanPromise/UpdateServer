# app.py
from flask import Flask, send_from_directory, jsonify, request
from flask_socketio import SocketIO, emit, join_room, leave_room
import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'supersecretkey'

# Use threading to avoid eventlet/ssl issues on Python 3.13
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

# Store users and APK info
users = {}       # username -> device info
apk_version = "v1"
apk_file = "app-release.apk"  # Make sure this exists in the same folder

# ----------------------------
# HTTP routes
# ----------------------------
@app.route('/')
def index():
    return jsonify({"status": "Update server running"})


@app.route('/apk/<filename>')
def serve_apk(filename):
    # Serve APK file from the local directory
    if os.path.exists(filename):
        return send_from_directory(os.getcwd(), filename)
    return "APK not found", 404


@app.route('/users')
def list_users():
    # Return all logged-in usernames
    return jsonify(list(users.keys()))


# ----------------------------
# SocketIO events
# ----------------------------
@socketio.on('connect')
def handle_connect():
    emit('status', f'Connected to update server. APK version: {apk_version}')


@socketio.on('register')
def handle_register(data):
    username = data.get('username', 'unknown')
    device_id = data.get('device_id', 'unknown')
    version = data.get('version', 'unknown')

    users[username] = {"device_id": device_id, "version": version}
    emit('users_list', list(users.keys()), broadcast=True)
    emit('apk_update', {"version": apk_version, "url": f"/apk/{apk_file}"})
    print(f"Registered user: {username} -> {users[username]}")


@socketio.on('logout_all')
def handle_logout_all():
    users.clear()
    emit('users_list', list(users.keys()), broadcast=True)
    print("All users signed out.")


@socketio.on('disconnect')
def handle_disconnect():
    print("A client disconnected.")


# ----------------------------
# Run server
# ----------------------------
if __name__ == '__main__':
    # Bind to 0.0.0.0 for Render
    socketio.run(app, host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
