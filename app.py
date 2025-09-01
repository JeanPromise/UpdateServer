from flask import Flask, request, jsonify, send_from_directory
import os

app = Flask(__name__)

# Store users in memory for now; could be extended to a file/db
registered_users = {}

# Serve index.html from root
@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

# Register a new user
@app.route('/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username')
    password = data.get('password')

    if not username or not password:
        return jsonify({'status': 'error', 'message': 'Username and password required'}), 400

    if username in registered_users:
        return jsonify({'status': 'error', 'message': 'Username already exists'}), 400

    registered_users[username] = password
    return jsonify({'status': 'success', 'message': f'User {username} registered'}), 200

# Login user
@app.route('/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')

    stored_password = registered_users.get(username)
    if stored_password and stored_password == password:
        return jsonify({'status': 'success', 'message': f'Welcome {username}'}), 200
    return jsonify({'status': 'error', 'message': 'Invalid credentials'}), 401

# List registered users
@app.route('/users', methods=['GET'])
def users():
    return jsonify(list(registered_users.keys()))

# Endpoint for APK info submission
@app.route('/device', methods=['POST'])
def device_info():
    data = request.json
    print("Received device info:", data)
    return jsonify({'status': 'ok'}), 200

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
