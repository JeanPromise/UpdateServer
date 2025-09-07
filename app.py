from flask import Flask, send_from_directory, render_template_string

app = Flask(__name__)

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

@app.route('/watch/<movie>')
def watch(movie):
    return f"<h1>Now playing: {movie}</h1>"

if __name__ == "__main__":
    app.run(debug=True, port=5000)
