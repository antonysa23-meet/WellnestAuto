import os
from flask import Flask, request, jsonify
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY")

@app.route("/run", methods=["POST"])
def run():
    data = request.get_json()
    input_value = data.get("data")
    output = f"Processed: {input_value}"
    return jsonify({"output": output})

if __name__ == "__main__":
    app.run(debug=True)
