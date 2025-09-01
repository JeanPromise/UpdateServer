from flask import Flask, render_template, jsonify
from flask_sockets import Sockets
import json
import threading

app = Flask(__name__)
sockets = Sockets(app)

# Store users
connected_users = {}  # key: device_id, value: {username, version}

lock = threading.Lock()

# ---------------------------
# WebSocket endpoint
# ---------------------------
@sockets.route('/ws')
def echo_socket(ws):
    while not ws.closed:
        message = ws.receive()
        if message:
            # Expecting: "device_id:XXX;username:YYY;version:ZZZ"
            data = {}
            try:
                for part in message.split(";"):
                    k, v = part.split(":")
                    data[k] = v
                device_id = data.get("device_id")
                username = data.get("username", "unknown")
                version = data.get("version", "unknown")
                with lock:
                    connected_users[device_id] = {"username": username, "version": version}
                print(f"Registered: {username} ({device_id})")
            except Exception as e:
                print("Failed parsing message:", message, e)
    return ""

# ---------------------------
# HTTP route to see users
# ---------------------------
@app.route('/')
def index():
    with lock:
        users = list(connected_users.values())
    return render_template("index.html", users=users)

# ---------------------------
# Serve JSON for programmatic access
# ---------------------------
@app.route('/users')
def users_json():
    with lock:
        return jsonify(connected_users)

# ---------------------------
# Run server
# ---------------------------
if __name__ == '__main__':
    from gevent import pywsgi
    from geventwebsocket.handler import WebSocketHandler

    server = pywsgi.WSGIServer(("0.0.0.0", 5000), app, handler_class=WebSocketHandler)
    print("Server started on http://0.0.0.0:5000")
    server.serve_forever()
