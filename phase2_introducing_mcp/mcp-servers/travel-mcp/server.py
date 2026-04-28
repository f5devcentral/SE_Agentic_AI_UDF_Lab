import logging
import os
from typing import List, Dict, Any

import httpx
import psycopg2
from fastmcp import FastMCP

from shared.tracing import init_tracing, tracer, inject_trace_headers, start_span_from_context
from shared.logging import configure_logging
from shared.metrics import register_metrics

# ── Configuration ──────────────────────────────────────────────────────────
DB_HOST = os.getenv("DB_HOST", "postgres")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "travel")
DB_USER = os.getenv("DB_USER", "travel")
DB_PASSWORD = os.getenv("DB_PASSWORD", "travelpass")

FLIGHTS_API_URL = os.getenv("FLIGHTS_API_URL", "http://10.1.20.40:31907/")
HOTELS_API_URL = os.getenv("HOTELS_API_URL", "http://10.1.20.40:31647/")
API_TIMEOUT = int(os.getenv("API_TIMEOUT", "10"))

# ── Setup OpenTelemetry ────────────────────────────────────────────────────
SERVICE_NAME = "travel-mcp-server"
SERVICE_NAME = "travel-mcp-server"

# Logging
logger = configure_logging(SERVICE_NAME)

# Fake Flask app not needed → but metrics/tracing expect app
from flask import Flask
app = Flask(__name__)

# Metrics
meter = register_metrics(app, SERVICE_NAME)

# Tracing
init_tracing(app, SERVICE_NAME)

# ── Initialize MCP Server ──────────────────────────────────────────────────
mcp = FastMCP(SERVICE_NAME)
http_client = httpx.AsyncClient(timeout=API_TIMEOUT)


# ── PostgreSQL Connection ──────────────────────────────────────────────────
def get_db_connection():
    try:
        conn = psycopg2.connect(
            host=DB_HOST,
            port=DB_PORT,
            database=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD
        )
        return conn
    except Exception as e:
        logger.error(f"Database connection failed: {e}")
        raise


# ── Tool: Search Flights (REST API) ───────────────────────────────────────
@mcp.tool()
async def search_flights(origin: str, destination: str, date: str, trace_context: Dict[str, str] = None) -> List[Dict[str, Any]]:
    logger.info(f"Searching flights: {origin} → {destination} on {date}")

    # Start a span from incoming trace context
    with start_span_from_context(None, "search_flights", trace_context) as span:
        headers = inject_trace_headers(span, trace_context)

        try:
            response = await http_client.get(
                FLIGHTS_API_URL,
                params={"origin": origin, "destination": destination, "date": date},
                headers=headers
            )
            response.raise_for_status()
            flights = response.json()
            if not isinstance(flights, list):
                logger.error(f"Flights API returned non-list: {type(flights)}")
                return []
            return sorted(flights, key=lambda x: x.get("price", 9999))

        except httpx.HTTPStatusError as e:
            logger.error(f"Flights API HTTP error: {e.response.status_code}")
            return []
        except httpx.RequestError as e:
            logger.error(f"Flights API connection error: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error in search_flights: {e}")
            return []


# ── Tool: Search Hotels (REST API) ────────────────────────────────────────
@mcp.tool()
async def search_hotels(city: str, checkin: str, checkout: str, trace_context: Dict[str, str] = None) -> List[Dict[str, Any]]:
    logger.info(f"Searching hotels in {city}: {checkin} → {checkout}")

    with start_span_from_context(None, "search_hotels", trace_context) as span:
        headers = inject_trace_headers(span, trace_context)

        try:
            response = await http_client.get(
                HOTELS_API_URL,
                params={"city": city, "checkin": checkin, "checkout": checkout},
                headers=headers
            )
            response.raise_for_status()
            hotels = response.json()
            if not isinstance(hotels, list):
                logger.error(f"Hotels API returned non-list: {type(hotels)}")
                return []
            return sorted(hotels, key=lambda x: x.get("price_per_night", 9999))

        except httpx.HTTPStatusError as e:
            logger.error(f"Hotels API HTTP error: {e.response.status_code}")
            return []
        except httpx.RequestError as e:
            logger.error(f"Hotels API connection error: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error in search_hotels: {e}")
            return []


# ── Tool: Search Activities (PostgreSQL) ──────────────────────────────────
@mcp.tool()
def search_activities(city: str, trace_context: Dict[str, str] = None) -> List[Dict[str, Any]]:
    logger.info(f"Searching activities in {city}")

    with start_span_from_context(tracer, "search_activities", trace_context) as span:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, title, description, city FROM activities WHERE LOWER(city)=LOWER(%s) ORDER BY id LIMIT 10",
                (city,)
            )
            rows = cursor.fetchall()

            if not rows:
                cursor.execute(
                    "SELECT id, title, description, city FROM activities WHERE city IS NULL ORDER BY id LIMIT 10"
                )
                rows = cursor.fetchall()

            activities = [{"id": r[0], "title": r[1], "description": r[2], "city": r[3] or city} for r in rows]
            cursor.close()
            conn.close()
            return activities

        except Exception as e:
            logger.error(f"Database error in search_activities: {e}")
            return []


# ── Health Check ───────────────────────────────────────────────────────────
@mcp.tool()
def health_check() -> Dict[str, Any]:
    status = {"service": SERVICE_NAME, "status": "ok", "dependencies": {}}

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.close()
        conn.close()
        status["dependencies"]["postgresql"] = "ok"
    except Exception as e:
        status["dependencies"]["postgresql"] = f"error: {e}"
        status["status"] = "degraded"

    status["dependencies"]["flights_api"] = {"url": FLIGHTS_API_URL, "status": "configured"}
    status["dependencies"]["hotels_api"] = {"url": HOTELS_API_URL, "status": "configured"}

    return status


# ── Run Server ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    logger.info("=" * 60)
    logger.info(f"Starting {SERVICE_NAME} with REST API backends")
    logger.info(f"Flights API: {FLIGHTS_API_URL}")
    logger.info(f"Hotels API:  {HOTELS_API_URL}")
    logger.info(f"PostgreSQL:  {DB_HOST}:{DB_PORT}/{DB_NAME}")
    logger.info("=" * 60)

    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)
