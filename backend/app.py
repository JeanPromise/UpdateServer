# app.py
from flask import Flask, render_template, send_from_directory
from flask_sockets import Sockets
import threading

app = Flask(__name__)
sockets = Sockets(app)

# Keep track of connected devices
connected_devices = {}

# --- WebSocket endpoint for devices ---
@sockets.route('/ws')
def device_socket(ws):
    device_id = None
    try:
        while not ws.closed:
            message = ws.receive()
            if message:
                if message.startswith("DEVICE_INFO:"):
                    device_id = message.split("DEVICE_INFO:")[1]
                    connected_devices[device_id] = ws
                    print(f"[+] Device connected: {device_id}")
                else:
                    print(f"[Device {device_id}] {message}")
    finally:
        if device_id and device_id in connected_devices:
            del connected_devices[device_id]
            print(f"[-] Device disconnected: {device_id}")

# --- Admin dashboard ---
@app.route('/')
def index():
    return render_template('index.html', devices=list(connected_devices.keys()))

# --- Send command to device ---
@app.route('/send/<device_id>/<command>')
def send_command(device_id, command):
    ws = connected_devices.get(device_id)
    if ws:
        ws.send(command)
        return f"Sent '{command}' to {device_id}"
    return f"Device {device_id} not connected."

# --- Serve APK files ---
@app.route('/apks/<path:filename>')
def serve_apk(filename):
    return send_from_directory('apks', filename)

if __name__ == "__main__":
    from gevent import pywsgi
    from geventwebsocket.handler import WebSocketHandler
    print("[*] Admin server running at http://0.0.0.0:5000")
    server = pywsgi.WSGIServer(("0.0.0.0", 5000), app, handler_class=WebSocketHandler)
    server.serve_forever()
