from flask import Flask, render_template, request
import requests
import psycopg2
from shared.metrics import register_metrics
from shared.tracing import init_tracing
from shared.logging import configure_logging
import os
import logging

app = Flask(__name__)
SERVICE = "frontend"
logger = configure_logging(SERVICE)
meter = register_metrics(app, SERVICE)
init_tracing(app, SERVICE)

from opentelemetry.instrumentation.requests import RequestsInstrumentor
RequestsInstrumentor().instrument()

PG_HOST = os.getenv("PG_HOST", "postgres")
PG_USER = os.getenv("PG_USER", "travel")
PG_PASSWORD = os.getenv("PG_PASSWORD", "travelpass")
PG_DB = os.getenv("PG_DB", "travel")

def get_activities(city):
    try:
        conn = psycopg2.connect(host=PG_HOST, user=PG_USER, password=PG_PASSWORD, dbname=PG_DB)
        cur = conn.cursor()
        cur.execute("SELECT id, title, description, city FROM activities WHERE LOWER(city) = LOWER(%s) LIMIT 10;", (city,))
        rows = cur.fetchall()
        if not rows:
            cur.execute("SELECT id, title, description, city FROM activities LIMIT 10;")
            rows = cur.fetchall()
        cur.close()
        conn.close()
        return [{"id": r[0], "title": r[1], "description": r[2], "city": r[3]} for r in rows]
    except Exception as e:
        logger.error(f"DB error: {e}")
        return []

@app.route("/", methods=["GET", "POST"])
def home():
    results = None
    form_data = {}

    if request.method == "POST":
        form_data = {
            "origin": request.form.get("origin", "").strip(),
            "destination": request.form.get("destination", "").strip(),
            "departure_date": request.form.get("departure_date", "").strip(),
            "return_date": request.form.get("return_date", "").strip(),
        }

        try:
            flights = requests.get("http://flights:5002/", params={
                "origin": form_data["origin"],
                "destination": form_data["destination"],
                "date": form_data["departure_date"]
            }, timeout=5).json()
        except Exception as e:
            logger.error(f"Flights service error: {e}")
            flights = []

        try:
            hotels = requests.get("http://hotels:5001/", params={
                "city": form_data["destination"],
                "checkin": form_data["departure_date"],
                "checkout": form_data["return_date"]
            }, timeout=5).json()
        except Exception as e:
            logger.error(f"Hotels service error: {e}")
            hotels = []

        activities = get_activities(form_data["destination"])

        results = {
            "flights": flights,
            "hotels": hotels,
            "activities": activities
        }

    return render_template("index.html", results=results, form_data=form_data)

@app.route("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
