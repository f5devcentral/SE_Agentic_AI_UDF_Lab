"""
orchestrator/config.py
─────────────────────
All environment variables and constants. Import from here everywhere else.
Nothing else should call os.getenv() directly.
"""
import os

# ── Service identity ──────────────────────────────────────────────────────────
SERVICE_NAME     = "orchestrator"
SERVICE_VERSION  = "4.0.0"

# ── LLM ──────────────────────────────────────────────────────────────────────
OLLAMA_URL        = os.getenv("OLLAMA_URL",         "http://ollama:11434")
LLM_MODEL         = os.getenv("LLM_MODEL",          "llama3.2:3b")
OLLAMA_TIMEOUT    = int(os.getenv("OLLAMA_TIMEOUT", "120"))

# ── Downstream agents ─────────────────────────────────────────────────────────
TRAVEL_AGENT_URL   = os.getenv("TRAVEL_AGENT_URL",   "http://travel-agent:8001")
FLIGHT_AGENT_URL   = os.getenv("FLIGHT_AGENT_URL",   "http://flight-agent:8002")
HOTEL_AGENT_URL    = os.getenv("HOTEL_AGENT_URL",    "http://hotel-agent:8003")
ACTIVITY_AGENT_URL = os.getenv("ACTIVITY_AGENT_URL", "http://activity-agent:8004")
WEATHER_AGENT_URL  = os.getenv("WEATHER_AGENT_URL",  "http://weather-agent:8005")

AGENT_URLS = {
    "travel":   TRAVEL_AGENT_URL,
    "flight":   FLIGHT_AGENT_URL,
    "hotel":    HOTEL_AGENT_URL,
    "activity": ACTIVITY_AGENT_URL,
    "weather":  WEATHER_AGENT_URL,
}

# ── MCP servers (orchestrator-only — agents never read these) ─────────────────
TRAVEL_MCP_URL  = os.getenv("TRAVEL_MCP_URL",  "http://travel-mcp:8000/mcp")
WEATHER_MCP_URL = os.getenv("WEATHER_MCP_URL", "http://weather-mcp:8001/mcp")

# ── RAG ───────────────────────────────────────────────────────────────────────
RAG_BASE      = os.getenv("RAG_URL",       "http://rag-service:8000")
RAG_DOC_LIMIT = int(os.getenv("RAG_DOC_LIMIT", "3"))
RAG_DOC_CHARS = int(os.getenv("RAG_DOC_CHARS", "220"))

# ── Self ──────────────────────────────────────────────────────────────────────
ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:9000")

# ── Season → representative travel date ──────────────────────────────────────
SEASON_DATE = {
    "spring": "2025-04-15",
    "summer": "2025-07-15",
    "autumn": "2025-10-15",
    "winter": "2025-12-20",
}

# ── Season → valid month range (inclusive) ────────────────────────────────────
# Used by the budget loop to keep alternate dates within the declared season.
SEASON_BOUNDS = {
    "spring": (3, 5),
    "summer": (6, 8),
    "autumn": (9, 11),
    "fall":   (9, 11),
    "winter": (12, 2),   # wraps year-end
}

# ── Agentic budget loop ───────────────────────────────────────────────────────
# Max number of flight+hotel re-search iterations before giving up and
# returning the closest combination found with a warning.
MAX_BUDGET_ITERATIONS = int(os.getenv("MAX_BUDGET_ITERATIONS", "3"))


# ── F5 AI Security (CalypsoAI) Guardrail ─────────────────────────────────────
# Set F5GUARDRAILS_ENABLED=true and provide TOKEN + PROJECT UUID to activate.
# See services/orchestrator/guardrail.py for full documentation.
F5GUARDRAILS_ENABLED = os.getenv("F5GUARDRAILS_ENABLED", "false").lower() == "true"
F5GUARDRAILS_URL     = os.getenv("F5GUARDRAILS_URL",     "https://www.us1.calypsoai.app")
F5GUARDRAILS_TOKEN   = os.getenv("F5GUARDRAILS_TOKEN",   "")
F5GUARDRAILS_PROJECT = os.getenv("F5GUARDRAILS_PROJECT", "")  # UUID e.g. 019d6d65-8fc4-707a-...
F5GUARDRAILS_TIMEOUT = int(os.getenv("F5GUARDRAILS_TIMEOUT", "10"))

# ═════════════════════════════════════════════════════════════════════════════
# ╔══════════════════════════════════════════════════════════╗
# ║      CENTRAL SKILL → MCP GOVERNANCE REGISTRY            ║
# ║                                                          ║
# ║  Keyed by MCP TOOL NAME (what agents declare in          ║
# ║  requires_mcp_tools[]).                                  ║
# ║                                                          ║
# ║  The orchestrator reads each agent card's                ║
# ║  requires_mcp_tools list, looks up every tool here,      ║
# ║  calls the MCP server, and injects the result.           ║
# ║  Agents never call MCP directly.                         ║
# ╚══════════════════════════════════════════════════════════╝
#
#   tool_name → {
#       mcp_server:  logical name for logging
#       mcp_url:     resolved endpoint
#       result_key:  key in mcp_results dict sent to the agent
#   }
# ═════════════════════════════════════════════════════════════════════════════

SKILL_MCP_MAP = {
    # travel-mcp tools
    "search_flights": {
        "mcp_server": "travel-mcp",
        "mcp_url":    TRAVEL_MCP_URL,
        "result_key": "flights",
    },
    "search_hotels": {
        "mcp_server": "travel-mcp",
        "mcp_url":    TRAVEL_MCP_URL,
        "result_key": "hotels",
    },
    "search_activities": {
        "mcp_server": "travel-mcp",
        "mcp_url":    TRAVEL_MCP_URL,
        "result_key": "activities",
    },
    # weather-mcp tools
    "get_weather_forecast": {
        "mcp_server": "weather-mcp",
        "mcp_url":    WEATHER_MCP_URL,
        "result_key": "weather",
    },
}

# ═════════════════════════════════════════════════════════════════════════════
# ╔══════════════════════════════════════════════════════════╗
# ║      SKILL RECIPE CATALOGUE                              ║
# ║                                                          ║
# ║  Human-readable description of every skill the           ║
# ║  orchestrator can dispatch.  Logged before each          ║
# ║  A2A tasks/send so demo observers understand what        ║
# ║  is about to happen and why.                             ║
# ╚══════════════════════════════════════════════════════════╝
#
#   skill_id → {
#       verb:     short action word shown in the banner
#       summary:  one sentence — what this skill does
#       inputs:   which data it consumes (mcp_results keys or context fields)
#       outputs:  shape of the artifact it produces
#       why:      why the orchestrator selects this skill at this point
#   }
# ═════════════════════════════════════════════════════════════════════════════

SKILL_RECIPES = {
    "rank_flights": {
        "verb":    "RANK",
        "summary": "Apply LLM reasoning to score and order retrieved flights against user preferences and budget.",
        "inputs":  ["mcp_results.flights", "user_context.budget_eur", "user_context.interests"],
        "outputs": "ranked_flights[] + best_choice index + recommendation text",
        "why":     "Translates raw MCP flight data into a traveller-friendly ranked decision.",
    },
    "rank_hotels": {
        "verb":    "RANK",
        "summary": "Score hotels using LLM reasoning, weighing rating, amenities, proximity to interests, and budget fit.",
        "inputs":  ["mcp_results.hotels", "user_context.budget_eur", "user_context.interests"],
        "outputs": "ranked_hotels[] + recommendation text + within_budget flags",
        "why":     "Converts raw hotel listings into a personalised recommendation.",
    },
    "suggest_activities": {
        "verb":    "SUGGEST",
        "summary": "Cross-reference activity catalogue with weather forecast and user interests to produce a curated itinerary.",
        "inputs":  ["mcp_results.activities", "mcp_results.weather", "user_context.interests"],
        "outputs": "selected_activities[] with fit_score and weather_suitable flags",
        "why":     "Produces the daily activity plan, skipping bad-weather days for outdoor options.",
    },
    "get_forecast": {
        "verb":    "FORECAST",
        "summary": "Analyse raw MCP weather forecast and translate it into traveller-friendly guidance.",
        "inputs":  ["mcp_results.weather", "user_context.destination"],
        "outputs": "summary, good_days[], bad_days[], packing_tips[], activity_impact",
        "why":     "Gives the user actionable weather intelligence rather than raw numbers.",
    },
}
