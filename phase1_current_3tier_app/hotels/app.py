from flask import Flask, jsonify, request
from shared.metrics import register_metrics
from shared.tracing import init_tracing
from shared.logging import configure_logging
import random
import logging
from datetime import datetime

app = Flask(__name__)
SERVICE = "hotels"
logger = configure_logging(SERVICE)
register_metrics(app, SERVICE)
init_tracing(app, SERVICE)

from opentelemetry.instrumentation.requests import RequestsInstrumentor
RequestsInstrumentor().instrument()

HOTEL_NAMES = ["Grand Palace", "Ocean View", "City Lights", "Royal Stay",
               "The Metropolitan", "Harbor Inn", "Central Suites", "The Boulevard"]
AMENITIES = ["Pool", "Gym", "Spa", "Restaurant", "Bar", "Parking", "WiFi", "Room Service"]

@app.route("/")
def hotels():
    city = request.args.get("city", "Unknown")
    checkin = request.args.get("checkin", "")
    checkout = request.args.get("checkout", "")

    # Calculate number of nights for total price
    nights = 1
    try:
        d1 = datetime.strptime(checkin, "%Y-%m-%d")
        d2 = datetime.strptime(checkout, "%Y-%m-%d")
        nights = max(1, (d2 - d1).days)
    except Exception:
        pass

    results = []
    used_names = random.sample(HOTEL_NAMES, min(random.randint(3, 5), len(HOTEL_NAMES)))
    for name in used_names:
        price_per_night = random.randint(80, 350)
        results.append({
            "name": f"{name} {city}",
            "city": city,
            "stars": random.randint(3, 5),
            "price_per_night": price_per_night,
            "total_price": price_per_night * nights,
            "nights": nights,
            "amenities": random.sample(AMENITIES, random.randint(3, 6)),
            "rating": round(random.uniform(7.0, 9.9), 1)
        })

    results.sort(key=lambda x: x["price_per_night"])
    return jsonify(results)

@app.route("/health")
def health():
    logger.debug("Health check requested")
    return {"status": "ok"}

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
