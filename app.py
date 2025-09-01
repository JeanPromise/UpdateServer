from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit

app = Flask(__name__)
app.config['SECRET_KEY'] = 'supersecretkey'

# Use eventlet for WebSocket support
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

# Store logged-in usernames
logged_in_users = set()

# ------------------------
# HTTP routes
# ------------------------
@app.route('/')
def index():
    return "WebSocket server running. Users will appear in /users."

@app.route('/users')
def get_users():
    return jsonify(list(logged_in_users))

@app.route('/logout_all', methods=['POST'])
def logout_all():
    logged_in_users.clear()
    socketio.emit('force_logout', {'msg': 'All users signed out!'})
    return jsonify({'status': 'ok', 'message': 'All users signed out'})

# ------------------------
# WebSocket events
# ------------------------
@socketio.on('connect')
def handle_connect():
    username = request.args.get('username', 'unknown')
    logged_in_users.add(username)
    emit('user_list', list(logged_in_users), broadcast=True)
    print(f"{username} connected. Total users: {len(logged_in_users)}")

@socketio.on('disconnect')
def handle_disconnect():
    username = request.args.get('username', 'unknown')
    if username in logged_in_users:
        logged_in_users.remove(username)
        emit('user_list', list(logged_in_users), broadcast=True)
    print(f"{username} disconnected. Total users: {len(logged_in_users)}")

# ------------------------
# Run
# ------------------------
if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)
