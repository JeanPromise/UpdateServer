import os
from flask import Flask, send_from_directory
from flask_sock import Sock

app = Flask(__name__)
sock = Sock(app)

clients = set()

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@sock.route('/ws')
def websocket(ws):
    clients.add(ws)
    try:
        while True:
            msg = ws.receive()
            if msg is None:
                break
    finally:
        clients.remove(ws)
        # broadcast updated users count
        for c in clients.copy():
            try:
                c.send(f"USERS_UPDATE:{len(clients)}")
            except:
                clients.remove(c)
