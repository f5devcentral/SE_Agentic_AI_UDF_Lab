import logging
import os

from flask import Flask, render_template, request

from mcp_client import (
    check_mcp_server,
    list_mcp_tools,
    search_flights,
    search_hotels,
    search_activities,
    get_weather_forecast
)

from shared.logging import configure_logging
from shared.metrics import register_metrics
from shared.tracing import init_tracing

# ──────────────────────────────────────────────────────────────
# Environment Variables
# ──────────────────────────────────────────────────────────────

TRAVEL_MCP_URL = os.getenv("TRAVEL_MCP_URL", "http://travel-mcp:8000/mcp")
WEATHER_MCP_URL = os.getenv("WEATHER_MCP_URL", "http://weather-mcp:8001/mcp")
OTEL_EXPORTER_OTLP_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")

SERVICE = "frontend"

app = Flask(__name__)

logger = configure_logging(SERVICE)
meter = register_metrics(app, SERVICE)
init_tracing(app, SERVICE)

logger.info(f"TRAVEL_MCP_URL={TRAVEL_MCP_URL}")
logger.info(f"WEATHER_MCP_URL={WEATHER_MCP_URL}")
logger.info(f"OTEL_EXPORTER_OTLP_ENDPOINT={OTEL_EXPORTER_OTLP_ENDPOINT}")


# ──────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────

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

        origin = form_data["origin"]
        dest = form_data["destination"]
        dep = form_data["departure_date"]
        ret = form_data["return_date"]

        logger.info(f"Search: {origin} → {dest} ({dep} → {ret})")

        try:
            flights = search_flights(TRAVEL_MCP_URL, origin, dest, dep)
            hotels = search_hotels(TRAVEL_MCP_URL, dest, dep, ret)
            activities = search_activities(TRAVEL_MCP_URL, dest)
            weather = get_weather_forecast(WEATHER_MCP_URL, dest, dep)

            results = {
                "flights": flights or [],
                "hotels": hotels or [],
                "activities": activities or [],
                "weather": weather or {},
            }

        except Exception as e:
            logger.error(f"MCP call failed: {e}")
            results = {"flights": [], "hotels": [], "activities": [], "weather": {}}

    return render_template("index.html", results=results, form_data=form_data)


@app.route("/health")
def health():
    return {"status": "ok"}

@app.route("/mcp-status")
def mcp_status():
    """
    Diagnostic endpoint to check MCP connectivity and available tools.
    """
    return {
        "travel_mcp_url": TRAVEL_MCP_URL,
        "weather_mcp_url": WEATHER_MCP_URL,
        "travel_mcp_reachable": check_mcp_server(TRAVEL_MCP_URL),
        "weather_mcp_reachable": check_mcp_server(WEATHER_MCP_URL),
        "travel_tools": list_mcp_tools(TRAVEL_MCP_URL),
        "weather_tools": list_mcp_tools(WEATHER_MCP_URL),
    }


# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
