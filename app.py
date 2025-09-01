from flask import Flask, render_template, request, jsonify
from flask_sockets import Sockets
import gevent
from gevent import pywsgi
from geventwebsocket.handler import WebSocketHandler
import os

app = Flask(__name__)
sockets = Sockets(app)

clients = set()  # Connected WebSocket clients
users_online = {}  # username -> ws

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.route('/')
def index():
    return open('index.html').read()

@app.route('/upload_apk', methods=['POST'])
def upload_apk():
    if 'apk' not in request.files:
        return jsonify({'status':'error','msg':'No file'}),400
    file = request.files['apk']
    path = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(path)
    # broadcast APK to all connected clients
    for ws in clients:
        try:
            ws.send(f"apk:/uploads/{file.filename}")
        except Exception as e:
            print("Error sending apk:", e)
    return jsonify({'status':'ok','msg':'APK uploaded'})


@sockets.route('/ws')
def echo_socket(ws):
    clients.add(ws)
    try:
        while not ws.closed:
            msg = ws.receive()
            if msg:
                # detect user online messages
                if msg.startswith("user_online:"):
                    username = msg.split(';')[0].split(':')[1]
                    users_online[username] = ws
                    broadcast_user_list()
                # handle info requests
                elif msg == "info":
                    send_user_list(ws)
    except Exception as e:
        print("WebSocket error:", e)
    finally:
        clients.discard(ws)
        # remove from users_online
        for user, w in list(users_online.items()):
            if w == ws:
                del users_online[user]
        broadcast_user_list()


def broadcast_user_list():
    user_list = list(users_online.keys())
    for ws in clients:
        try:
            ws.send("users:" + ",".join(user_list))
        except:
            pass


def send_user_list(ws):
    user_list = list(users_online.keys())
    try:
        ws.send("users:" + ",".join(user_list))
    except:
        pass


if __name__ == "__main__":
    server = pywsgi.WSGIServer(('', 5000), app, handler_class=WebSocketHandler)
    print("Server running on port 5000")
    server.serve_forever()
