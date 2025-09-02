from flask import Flask, render_template, jsonify

app = Flask(__name__)

# Store latest APK link here
LATEST_APK_LINK = "https://your-storage.com/app_v1.0.apk"

@app.route("/")
def index():
    return open("index.html").read()

@app.route("/update.json")
def update_json():
    return jsonify({"latest_apk": LATEST_APK_LINK})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
