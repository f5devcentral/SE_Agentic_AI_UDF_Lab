from flask import Flask, jsonify, request
from shared.metrics import register_metrics
from shared.tracing import init_tracing
from shared.logging import configure_logging
import random
from datetime import datetime, timedelta
import logging

app = Flask(__name__)
SERVICE = "flights"
logger = configure_logging(SERVICE)
register_metrics(app, SERVICE)
init_tracing(app, SERVICE)

from opentelemetry.instrumentation.requests import RequestsInstrumentor
RequestsInstrumentor().instrument()

AIRLINES = ["SkyJet", "AirNova", "AirFrance", "British Airways", "BlueWind", "EuroSky", "FlyFast"]

@app.route("/")
def flights():
    origin = request.args.get("origin", "Unknown")
    destination = request.args.get("destination", "Unknown")
    date = request.args.get("date", "")

    logger.debug(f"Received request: origin={origin}, destination={destination}, date={date}")

    results = []
    try:
        for _ in range(random.randint(3, 6)):
            hour = random.randint(5, 22)
            minute = random.choice([0, 15, 30, 45])
            duration = round(random.uniform(1.5, 12.0), 1)
            departure_time = f"{hour:02d}:{minute:02d}"
            arrival_hour = (hour + int(duration)) % 24
            arrival_time = f"{arrival_hour:02d}:{minute:02d}"
            results.append({
                "airline": random.choice(AIRLINES),
                "origin": origin,
                "destination": destination,
                "date": date,
                "departure_time": departure_time,
                "arrival_time": arrival_time,
                "duration_hours": duration,
                "stops": random.choice([0, 0, 0, 1, 1, 2]),
                "price": random.randint(120, 900)
            })

        results.sort(key=lambda x: x["price"])
        logger.debug(f"Flight generated: {flight}")
    except Exception:
        logger.exception("Error generating flights")

    return jsonify(results)

@app.route("/health")
def health():
    logger.debug("Health check requested")                     
    return {"status": "ok"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5002, debug=True)
