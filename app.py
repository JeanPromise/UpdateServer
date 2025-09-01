from flask import Flask, jsonify, request
from flask_sockets import Sockets

app = Flask(__name__)
sockets = Sockets(app)

# Store connected users in memory
connected_users = set()

@app.route('/')
def index():
    return jsonify({"message": "UpdateServer Running"}), 200

@sockets.route('/ws')
def echo_socket(ws):
    while not ws.closed:
        message = ws.receive()
        if message:
            connected_users.add(message)  # message = username
            ws.send(f"Hello {message}, connected!")
        # Optionally, broadcast users list:
        ws.send(f"Users online: {list(connected_users)}")

@app.route('/users')
def users():
    return jsonify({"users": list(connected_users)})

if __name__ == "__main__":
    from gevent import pywsgi
    from geventwebsocket.handler import WebSocketHandler

    server = pywsgi.WSGIServer(("0.0.0.0", 5000), app, handler_class=WebSocketHandler)
    print("Server running on port 5000")
    server.serve_forever()
