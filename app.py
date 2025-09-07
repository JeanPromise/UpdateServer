from flask import Flask, request, session, redirect

app = Flask(__name__)
app.secret_key = "supersecretkey"

# simple in-memory store
users = {}

@app.route("/", methods=["GET"])
def index():
    return open("index.html").read()

@app.route("/register", methods=["POST"])
def register():
    username = request.form.get("username")
    password = request.form.get("password")

    if not username or not password:
        return "REGISTRATION_FAILED: Missing fields"

    if username in users:
        return "REGISTRATION_FAILED: User exists"

    users[username] = password
    return "REGISTRATION_SUCCESS"

@app.route("/login", methods=["POST"])
def login():
    username = request.form.get("username")
    password = request.form.get("password")

    if username in users and users[username] == password:
        session["username"] = username
        return "LOGIN_SUCCESS"
    else:
        return "LOGIN_FAILED"

@app.route("/logout")
def logout():
    session.pop("username", None)
    return redirect("/")

@app.route("/apk")
def apk():
    return "<h3>APK Download</h3><p><a href='/static/myapp.apk'>Download Here</a></p>"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
