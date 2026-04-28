"""
orchestrator-direct/config.py
──────────────────────────────
All environment variables and constants for the non-agentic direct orchestrator.
Identical structure to orchestrator/config.py — agents and SKILL_RECIPES removed,
token-budget constants added.

The direct orchestrator:
  • runs the same Steps 1-6 (context, RAG, MCP governance) as the agentic version
  • replaces Step 7 A2A fan-out with a single Ollama LLM call
  • produces the same output shape so the frontend needs zero changes
"""
import os

# ── Service identity ──────────────────────────────────────────────────────────
SERVICE_NAME     = "orchestrator-direct"
SERVICE_VERSION  = "1.0.0"

# ── LLM ──────────────────────────────────────────────────────────────────────
OLLAMA_URL     = os.getenv("OLLAMA_URL",          "http://ollama:11434")
LLM_MODEL      = os.getenv("LLM_MODEL",           "llama3.2:3b")
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT",  "120"))

# Token budget for the single LLM call.
# Keep output tight — we want to demo lower token consumption vs agentic.
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "1024"))
LLM_NUM_CTX    = int(os.getenv("LLM_NUM_CTX",    "4096"))

# ── MCP servers (orchestrator-only — no agents) ───────────────────────────────
TRAVEL_MCP_URL  = os.getenv("TRAVEL_MCP_URL",  "http://travel-mcp:8000/mcp")
WEATHER_MCP_URL = os.getenv("WEATHER_MCP_URL", "http://weather-mcp:8001/mcp")

# ── RAG ───────────────────────────────────────────────────────────────────────
RAG_BASE      = os.getenv("RAG_URL",        "http://rag-service:8000")
RAG_DOC_LIMIT = int(os.getenv("RAG_DOC_LIMIT", "3"))
RAG_DOC_CHARS = int(os.getenv("RAG_DOC_CHARS", "220"))

# ── Self ──────────────────────────────────────────────────────────────────────
ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://orchestrator-direct:9001")

# ── Season → representative travel date ──────────────────────────────────────
SEASON_DATE = {
    "spring": "2025-04-15",
    "summer": "2025-07-15",
    "autumn": "2025-10-15",
    "winter": "2025-12-20",
}

# ── Season → valid month range (inclusive) ────────────────────────────────────
SEASON_BOUNDS = {
    "spring": (3, 5),
    "summer": (6, 8),
    "autumn": (9, 11),
    "fall":   (9, 11),
    "winter": (12, 2),
}

# ═════════════════════════════════════════════════════════════════════════════
# SKILL → MCP GOVERNANCE REGISTRY  (identical to agentic orchestrator)
# The direct orchestrator still fetches all MCP data — it just reasons over
# it itself rather than fanning out to specialist agents.
# ═════════════════════════════════════════════════════════════════════════════

SKILL_MCP_MAP = {
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
    "get_weather_forecast": {
        "mcp_server": "weather-mcp",
        "mcp_url":    WEATHER_MCP_URL,
        "result_key": "weather",
    },
}

# ── Simulated agent card for MCP resolution ───────────────────────────────────
# The direct orchestrator doesn't discover real agents, but resolve_mcp_for_agent()
# in mcp_gateway.py needs a card-shaped dict to know which MCP tools to fetch.
# We pass this synthetic card so the exact same gateway code is reused.
DIRECT_AGENT_CARD = {
    "name":            SERVICE_NAME,
    "mcp_provided_by": "orchestrator",
    "skills": [
        {
            "id":                 "plan_trip_direct",
            "requires_mcp_tools": list(SKILL_MCP_MAP.keys()),
        }
    ],
}

# ── F5 AI Security (CalypsoAI) Guardrail ─────────────────────────────────────
# Set F5GUARDRAILS_ENABLED=true and provide TOKEN + PROJECT UUID to activate.
# See services/orchestrator/guardrail.py for full documentation.
F5GUARDRAILS_ENABLED = os.getenv("F5GUARDRAILS_ENABLED", "false").lower() == "true"
F5GUARDRAILS_URL     = os.getenv("F5GUARDRAILS_URL",     "https://www.us1.calypsoai.app")
F5GUARDRAILS_TOKEN   = os.getenv("F5GUARDRAILS_TOKEN",   "")
F5GUARDRAILS_PROJECT = os.getenv("F5GUARDRAILS_PROJECT", "")  
F5GUARDRAILS_TIMEOUT = int(os.getenv("F5GUARDRAILS_TIMEOUT", "10"))
