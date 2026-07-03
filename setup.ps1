python -m venv venv
.\venv\Scripts\Activate.ps1

pip install flask gunicorn python-dotenv

@"
FLASK_ENV=development
SECRET_KEY=change-this-to-something-random
"@ | Out-File -Encoding utf8 .env

@"
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
"@ | Out-File -Encoding utf8 app.py

pip freeze > requirements.txt

@"
venv/
.env
__pycache__/
*.pyc
"@ | Out-File -Encoding utf8 .gitignore

git init
git add .
git commit -m "Initial Flask app setup"

Write-Host "Setup complete. Run '.\venv\Scripts\Activate.ps1' then 'python app.py' to start the server."