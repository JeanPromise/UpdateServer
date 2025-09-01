from flask import Flask, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
import eventlet
eventlet.monkey_patch()

app = Flask(__name__)
app.config['SECRET_KEY'] = 'supersecretkey'
socketio = SocketIO(app, cors_allowed_origins="*")

# ------------------------
# In-memory user registry
# ------------------------
# username -> websocket session id
connected_users = {}

# ------------------------
# Web routes
# ------------------------
@app.route('/')
def index():
    return "Server is running!"

@app.route('/users', methods=['GET'])
def list_users():
    return jsonify(list(connected_users.keys()))

# ------------------------
# WebSocket events
# ------------------------
@socketio.on('connect')
def handle_connect():
    print(f"New connection: {request.sid}")

@socketio.on('register')
def handle_register(data):
    """
    Client should send:
    { "username": "user1", "version": "1.0.0" }
    """
    username = data.get("username")
    if username:
        connected_users[username] = request.sid
        print(f"{username} registered with sid {request.sid}")
        emit('status', f"Registered as {username}")

@socketio.on('disconnect')
def handle_disconnect():
    # remove user from registry
    to_remove = [u for u, sid in connected_users.items() if sid == request.sid]
    for u in to_remove:
        del connected_users[u]
        print(f"{u} disconnected")

# ------------------------
# Admin commands
# ------------------------
@app.route('/logout_all', methods=['POST'])
def logout_all():
    """
    Send logout to all connected users.
    Optional: provide JSON with "usernames": ["user1", "user2"]
    If not provided, all users will be logged out.
    """
    data = request.get_json() or {}
    usernames = data.get("usernames")
    
    if usernames:
        # only selected users
        for u in usernames:
            sid = connected_users.get(u)
            if sid:
                socketio.emit('command', 'logout', to=sid)
    else:
        # broadcast to all
        for sid in connected_users.values():
            socketio.emit('command', 'logout', to=sid)
    
    return jsonify({"status": "ok", "users_targeted": usernames or "all"})

# ------------------------
# Run
# ------------------------
if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)
