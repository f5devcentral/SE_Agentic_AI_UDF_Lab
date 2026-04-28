import logging
import os
import random
from datetime import datetime, timedelta
from typing import Any, Dict

from fastmcp import FastMCP


# ── Shared Telemetry ──────────────────────────────────────────────
from shared.tracing import init_tracing, start_span_from_context, inject_trace_headers
from shared.logging import configure_logging
from shared.metrics import register_metrics
from opentelemetry.trace import SpanKind

from flask import Flask
app = Flask(__name__)

# ── Service setup ────────────────────────────────────────────────
SERVICE = "weather-mcp-server"
logger = configure_logging(SERVICE)
tracer = init_tracing(service_name=SERVICE)
meter = register_metrics(app, service_name=SERVICE)

tool_calls = meter.create_counter("mcp_tool_calls_total", description="MCP tool invocations")
tool_errors = meter.create_counter("mcp_tool_errors_total", description="MCP tool errors")



# ── Mock weather data ─────────────────────────────────────────────────────────
CONDITIONS = [
    {"label": "Sunny", "icon": "☀️", "precip_mm": 0},
    {"label": "Partly Cloudy", "icon": "⛅", "precip_mm": 0},
    {"label": "Cloudy", "icon": "☁️", "precip_mm": 0},
    {"label": "Light Rain", "icon": "🌦️", "precip_mm": 5},
    {"label": "Rain", "icon": "🌧️", "precip_mm": 15},
    {"label": "Thunderstorm", "icon": "⛈️", "precip_mm": 30},
    {"label": "Windy", "icon": "💨", "precip_mm": 0},
    {"label": "Foggy", "icon": "🌫️", "precip_mm": 0},
]

WARM_CITIES = {"barcelona", "rome", "madrid", "lisbon", "athens", "dubai", "miami"}
COLD_CITIES = {"oslo", "stockholm", "helsinki", "reykjavik", "moscow", "montreal"}


def _base_temp(city: str) -> tuple[int, int]:
    c = city.lower()
    if any(w in c for w in WARM_CITIES):
        return (18, 32)
    if any(w in c for w in COLD_CITIES):
        return (-5, 10)
    return (10, 22)  # temperate default


def _mock_forecast(city: str, start_date: str, days: int = 5) -> list[dict[str, Any]]:
    try:
        base = datetime.strptime(start_date, "%Y-%m-%d")
    except ValueError:
        base = datetime.utcnow()

    min_t, max_t = _base_temp(city)
    rng = random.Random(f"{city}{start_date}")

    forecast = []
    for i in range(days):
        day = base + timedelta(days=i)
        condition = rng.choice(CONDITIONS)
        temp_hi = rng.randint(min_t + 4, max_t)
        temp_lo = rng.randint(min_t, temp_hi - 3)
        forecast.append({
            "date": day.strftime("%Y-%m-%d"),
            "day_of_week": day.strftime("%A"),
            "condition": condition["label"],
            "icon": condition["icon"],
            "temp_high_c": temp_hi,
            "temp_low_c": temp_lo,
            "temp_high_f": round(temp_hi * 9 / 5 + 32),
            "temp_low_f": round(temp_lo * 9 / 5 + 32),
            "precipitation_mm": condition["precip_mm"],
            "humidity_pct": rng.randint(40, 85),
            "wind_kmh": rng.randint(5, 40),
        })
    return forecast


# ── FastMCP server ────────────────────────────────────────────────────────────
mcp = FastMCP(
    name=SERVICE,
    instructions=(
        "Weather forecast server. Use get_weather_forecast to retrieve a 5-day "
        "weather forecast for any city. Useful for travel planning."
    ),
)


@mcp.tool(
    description=(
        "Get a 5-day weather forecast for a city starting from a given date. "
        "Returns daily conditions, high/low temperatures (°C and °F), precipitation, "
        "humidity, and wind speed."
    )
)
def get_weather_forecast(city: str, date: str, trace_context: Dict[str, str] = None) -> dict[str, Any]:
    """
    Retrieve a 5-day mock weather forecast for the destination city.
    Accepts optional trace_context from upstream for end-to-end tracing.
    """
    with start_span_from_context(tracer, "get_weather_forecast", trace_context, kind=SpanKind.SERVER) as span:
        span.set_attribute("weather.city", city)
        span.set_attribute("weather.date", date)
        tool_calls.add(1, {"tool": "get_weather_forecast"})
        logger.info(f"get_weather_forecast called for: {city} from {date}")

        try:
            forecast = _mock_forecast(city, date, days=5)
            result = {
                "city": city,
                "forecast_from": date,
                "days": forecast,
                "summary": f"{forecast[0]['condition']} to start, "
                           f"highs around {forecast[0]['temp_high_c']}°C / "
                           f"{forecast[0]['temp_high_f']}°F",
            }
            span.set_attribute("weather.forecast_days", len(forecast))
            return result
        except Exception as e:
            tool_errors.add(1, {"tool": "get_weather_forecast"})
            logger.error(f"get_weather_forecast error: {e}")
            span.record_exception(e)
            raise


if __name__ == "__main__":
    port = int(os.getenv("PORT", 8001))
    logger.info(f"Starting {SERVICE} on port {port}")
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port, path="/mcp")
