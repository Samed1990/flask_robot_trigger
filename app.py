from flask import Flask, render_template, request, redirect, flash
import os
import requests
from datetime import datetime
import csv
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "supersecretkey")

FLOW_URL = os.getenv("FLOW_URL")
LAUNCH_KEY = os.getenv("LAUNCH_KEY")

csv_path = Path("logs/trigger_log.csv")

@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        key = request.form.get("key", "").strip()

        if not name or not key:
            flash("Vennligst fyll ut begge felt", "warning")
            return redirect("/")

        if key != LAUNCH_KEY:
            flash("Feil kode. Prøv igjen.", "danger")
            return redirect("/")

        # Call flow
        try:
            params = {
                "triggered_by": name,
                "trigger_time": datetime.utcnow().isoformat() + "Z",
                "source": "flask"
            }
            response = requests.get(FLOW_URL, params=params)
            if response.status_code in [200, 202]:
                _log_to_csv(name)
                flash("Flyten ble trigget og logget!", "success")
            else:
                flash(f"Feil ved kjøring. Statuskode: {response.status_code}", "danger")
        except Exception as e:
            flash(f"En feil oppstod: {e}", "danger")

        return redirect("/")

    return render_template("index.html")


def _log_to_csv(name):
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    row = [datetime.utcnow().replace(microsecond=0).isoformat() + "Z", name]
    new_file = not csv_path.exists()

    with csv_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(["Tidspunkt_UTC", "Navn"])
        writer.writerow(row)

if __name__ == "__main__":
    app.run(debug=True)
