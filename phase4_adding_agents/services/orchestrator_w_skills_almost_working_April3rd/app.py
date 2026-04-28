"""
services/orchestrator/app.py  —  A2A Orchestrator  v3.3.0
══════════════════════════════════════════════════════════

Enterprise Governance Pattern
──────────────────────────────
  • Orchestrator owns ALL MCP calls — agents never touch MCP servers directly
  • SKILL_MCP_MAP is the central governance registry:
        skill_id  →  (mcp_server_name, tool_name, mcp_url)
  • After agent discovery the orchestrator reads each agent card's
    "requires_mcp_tools" list, resolves them through the registry,
    calls the tools, and injects the data into the A2A tasks/send payload
  • Agents are pure reasoning units: receive data → think → return decision
  • Every step is logged with ╔═╗ banners for demo/trace visibility

New in v3.3
───────────
  • SKILL_MCP_MAP registry (central skill → tool binding)
  • Dynamic MCP dispatch driven by agent card metadata
  • TRAVEL_MCP_URL / WEATHER_MCP_URL env vars (orchestrator only)
  • Full governance audit log at every step
  • Weather agent now included in the A2A fan-out
"""

import json
import logging
import os
import re
import uuid
from typing import Any, Dict, List, Optional, Tuple

import requests
from flask import Flask, jsonify, request

from shared.a2a_protocol import (
    A2AMessage, Artifact, DataPart,
    ERR_INVALID_PARAMS, ERR_METHOD_NOT_FOUND,
    Task, TaskStatus,
    build_agent_card, build_tasks_send,
    get_artifact_data,
    jsonrpc_error, jsonrpc_success, parse_jsonrpc,
)
from shared.logging import configure_logging
from shared.metrics import register_metrics
import shared.tracing as tracing
from shared.tracing import init_tracing

# ═══════════════════════════════════════════════════════════════════════════
# Configuration
# ═══════════════════════════════════════════════════════════════════════════

SERVICE_NAME = "orchestrator"

OLLAMA_URL        = os.getenv("OLLAMA_URL",          "http://ollama:11434")
LLM_MODEL         = os.getenv("LLM_MODEL",           "llama3.2:1b")
OLLAMA_TIMEOUT    = int(os.getenv("OLLAMA_TIMEOUT",  "90"))

RAG_BASE           = os.getenv("RAG_URL",             "http://rag-service:8000")
TRAVEL_AGENT_URL   = os.getenv("TRAVEL_AGENT_URL",    "http://travel-agent:8001")
FLIGHT_AGENT_URL   = os.getenv("FLIGHT_AGENT_URL",    "http://flight-agent:8002")
HOTEL_AGENT_URL    = os.getenv("HOTEL_AGENT_URL",     "http://hotel-agent:8003")
ACTIVITY_AGENT_URL = os.getenv("ACTIVITY_AGENT_URL",  "http://activity-agent:8004")
WEATHER_AGENT_URL  = os.getenv("WEATHER_AGENT_URL",   "http://weather-agent:8005")

# MCP servers — ONLY the orchestrator has these env vars
TRAVEL_MCP_URL  = os.getenv("TRAVEL_MCP_URL",  "http://travel-mcp:8000/mcp")
WEATHER_MCP_URL = os.getenv("WEATHER_MCP_URL", "http://weather-mcp:8001/mcp")

RAG_DOC_LIMIT    = int(os.getenv("RAG_DOC_LIMIT", "3"))
RAG_DOC_CHARS    = int(os.getenv("RAG_DOC_CHARS", "220"))
ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL",  "http://orchestrator:9000")

# ─── Season → representative travel date ────────────────────────────────────
SEASON_DATE: Dict[str, str] = {
    "spring": "2025-04-15",
    "summer": "2025-07-15",
    "autumn": "2025-10-15",
    "winter": "2025-12-20",
}

# ═══════════════════════════════════════════════════════════════════════════
# ╔══════════════════════════════════════════════════════════╗
# ║         CENTRAL SKILL → MCP GOVERNANCE REGISTRY         ║
# ║                                                          ║
# ║  This is the ONLY place where skill IDs are bound to     ║
# ║  MCP server names and tool names.                        ║
# ║  Agents declare *what* they need (requires_mcp_tools).   ║
# ║  The orchestrator decides *how* to fulfil it.            ║
# ╚══════════════════════════════════════════════════════════╝
#
# Format:
#   skill_id → {
#       "mcp_server": logical name for logging,
#       "mcp_url":    resolved endpoint,
#       "tool":       MCP tool name,
#       "result_key": key used in mcp_results dict sent to agent,
#   }
# ═══════════════════════════════════════════════════════════════════════════

SKILL_MCP_MAP: Dict[str, Dict[str, str]] = {
    "rank_flights": {
        "mcp_server": "travel-mcp",
        "mcp_url":    TRAVEL_MCP_URL,
        "tool":       "search_flights",
        "result_key": "flights",
    },
    "rank_hotels": {
        "mcp_server": "travel-mcp",
        "mcp_url":    TRAVEL_MCP_URL,
        "tool":       "search_hotels",
        "result_key": "hotels",
    },
    "suggest_activities": {
        "mcp_server": "travel-mcp",
        "mcp_url":    TRAVEL_MCP_URL,
        "tool":       "search_activities",
        "result_key": "activities",
    },
    "get_forecast": {
        "mcp_server": "weather-mcp",
        "mcp_url":    WEATHER_MCP_URL,
        "tool":       "get_weather_forecast",
        "result_key": "weather",
    },
}

# ═══════════════════════════════════════════════════════════════════════════
# App bootstrap
# ═══════════════════════════════════════════════════════════════════════════

app = Flask(__name__)
configure_logging(SERVICE_NAME)
meter = register_metrics(app, SERVICE_NAME)
init_tracing(app, SERVICE_NAME)

logger = logging.getLogger(SERVICE_NAME)
logger.setLevel(logging.INFO)

# ═══════════════════════════════════════════════════════════════════════════
# Logging helpers  — banners make log tailing easy during demos
# ═══════════════════════════════════════════════════════════════════════════

W = 72   # banner width

def _box(title: str, char="═"):
    inner = f"  {title}  "
    pad   = max(0, W - len(inner) - 2)
    left  = pad // 2
    right = pad - left
    logger.info("╔%s╗", char * (W))
    logger.info("║%s%s%s║", " " * left, inner, " " * right)
    logger.info("╚%s╝", char * (W))

def _banner(title: str):
    pad = max(0, W - len(title) - 4)
    logger.info("┌─ %s %s", title, "─" * pad)

def _banner_end():
    logger.info("└%s", "─" * (W - 1))

def _row(label: str, value: Any = None, limit: int = 1200):
    if value is None:
        logger.info("│  %s", label)
    else:
        logger.info("│  %-32s %s", label + ":", _trunc(value, limit))

def _sep():
    logger.info("│  %s", "·" * (W - 4))

def _trunc(value, limit=1000):
    if value is None:
        return "None"
    if isinstance(value, (dict, list)):
        try:
            value = json.dumps(value, ensure_ascii=False)
        except Exception:
            value = str(value)
    else:
        value = str(value)
    return value if len(value) <= limit else value[:limit] + f"… [{len(value)-limit} chars]"

def count_tokens(text: str) -> int:
    return len(text.split()) if text else 0

def log_http_out(name, method, url, payload=None, headers=None):
    logger.info("[OUT→] %-18s %s %s", name.upper(), method, url)
    if headers:
        safe = {k: v for k, v in headers.items()
                if k.lower() not in {"authorization", "cookie", "x-api-key"}}
        if safe:
            logger.debug("[OUT→] %-18s HEADERS: %s", name.upper(), safe)
    if payload is not None:
        logger.info("[OUT→] %-18s PAYLOAD (%d bytes): %s",
                    name.upper(),
                    len(json.dumps(payload, ensure_ascii=False)),
                    _trunc(payload, 900))

def log_http_in(name, resp: requests.Response) -> Any:
    elapsed = resp.elapsed.total_seconds()
    try:
        body = resp.json()
        logger.info("[←IN ] %-18s HTTP %s  %.3fs  %d bytes",
                    name.upper(), resp.status_code, elapsed, len(resp.content))
        logger.info("[←IN ] %-18s RESPONSE: %s", name.upper(), _trunc(body, 1200))
        return body
    except Exception:
        body = resp.text
        logger.info("[←IN ] %-18s HTTP %s  %.3fs  TEXT: %s",
                    name.upper(), resp.status_code, elapsed, _trunc(body, 800))
        return body

def http_post_json(name, url, payload, timeout=60, headers=None):
    h = headers or {}
    log_http_out(name, "POST", url, payload, h)
    resp = requests.post(url, json=payload, headers=h, timeout=timeout)
    log_http_in(name, resp)
    resp.raise_for_status()
    return resp

def http_get(name, url, timeout=10, headers=None):
    h = headers or {}
    log_http_out(name, "GET", url, headers=h)
    resp = requests.get(url, headers=h, timeout=timeout)
    log_http_in(name, resp)
    resp.raise_for_status()
    return resp

def get_tracer():
    return tracing.get_tracer(SERVICE_NAME)

# ═══════════════════════════════════════════════════════════════════════════
# Context extraction
# ═══════════════════════════════════════════════════════════════════════════

CITY_ALIASES: Dict[str, str] = {
    "rome": "Rome", "roma": "Rome",
    "barcelona": "Barcelona", "madrid": "Madrid",
    "seville": "Seville", "sevilla": "Seville",
    "paris": "Paris", "lyon": "Lyon", "nice": "Nice", "marseille": "Marseille",
    "london": "London", "edinburgh": "Edinburgh",
    "berlin": "Berlin", "munich": "Munich", "münchen": "Munich",
    "amsterdam": "Amsterdam",
    "lisbon": "Lisbon", "lisboa": "Lisbon", "porto": "Porto",
    "athens": "Athens", "santorini": "Santorini",
    "dubai": "Dubai", "istanbul": "Istanbul", "prague": "Prague",
    "budapest": "Budapest", "vienna": "Vienna", "wien": "Vienna",
    "tokyo": "Tokyo", "bangkok": "Bangkok", "singapore": "Singapore",
    "new york": "New York", "los angeles": "Los Angeles",
    "milan": "Milan", "milano": "Milan", "florence": "Florence",
    "firenze": "Florence", "venice": "Venice", "venezia": "Venice",
    "naples": "Naples", "napoli": "Naples",
}

SEASON_KEYWORDS: Dict[str, str] = {
    "spring": "spring", "summer": "summer",
    "autumn": "autumn", "fall": "autumn", "winter": "winter",
    "january": "winter", "february": "winter",
    "march": "spring", "april": "spring", "may": "spring",
    "june": "summer", "july": "summer", "august": "summer",
    "september": "autumn", "october": "autumn", "november": "autumn",
    "december": "winter",
}

INTEREST_KEYWORDS: Dict[str, str] = {
    "museum": "museums", "museums": "museums",
    "shop": "shopping", "shopping": "shopping",
    "hiking": "hiking", "hike": "hiking",
    "beach": "beach", "beaches": "beach",
    "bike": "biking", "biking": "biking", "cycling": "biking",
    "nature": "nature", "art": "art", "gallery": "art",
    "food": "food", "cuisine": "food", "restaurant": "food",
    "nightlife": "nightlife", "history": "history", "historical": "history",
    "architecture": "architecture", "wine": "wine",
}

_BUDGET_RE = re.compile(
    r"(?:budget[^\d€$£]*|€|£|\$)?(\d[\d,\.]{1,8})\s*(?:euro|eur|€|gbp|usd|\$)?",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(
    r"(\d+|one|two|three|four|five|six|seven|a)\s*(week|day|night)",
    re.IGNORECASE,
)
_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4,
              "five": 5, "six": 6, "seven": 7, "a": 1}


def _find_cities(text_lower: str) -> List[str]:
    found: Dict[str, str] = {}
    for alias in sorted(CITY_ALIASES, key=len, reverse=True):
        if alias in text_lower and CITY_ALIASES[alias] not in found.values():
            found[alias] = CITY_ALIASES[alias]
    return list(dict.fromkeys(found.values()))


def extract_user_context(prompt: str) -> Dict[str, Any]:
    p      = prompt.lower()
    cities = _find_cities(p)
    origin: Optional[str] = None
    destination: Optional[str] = None

    from_m = re.search(r"\bfrom\s+([a-z\s]+?)(?:\s+to|\s+in|\s+for|\s+with|,|$)", p)
    to_m   = re.search(r"\bto\s+([a-z\s]+?)(?:\s+in|\s+for|\s+with|,|$)", p)

    if from_m:
        frag = from_m.group(1).strip()
        for alias, canon in CITY_ALIASES.items():
            if alias in frag:
                origin = canon; break
    if to_m:
        frag = to_m.group(1).strip()
        for alias, canon in CITY_ALIASES.items():
            if alias in frag:
                destination = canon; break

    if not origin and not destination and len(cities) >= 2:
        origin, destination = cities[0], cities[1]
    elif not destination and cities:
        destination = cities[0] if not origin else (cities[1] if len(cities) > 1 else cities[0])

    season: Optional[str] = None
    for kw, s in SEASON_KEYWORDS.items():
        if kw in p:
            season = s; break

    budget: Optional[int] = None
    for m in _BUDGET_RE.finditer(p):
        raw = m.group(1).replace(",","").replace(".","")
        try:
            val = int(raw)
            if 50 <= val <= 1_000_000:
                budget = max(budget or 0, val)
        except ValueError:
            pass

    duration: Optional[str] = None
    dm = _DURATION_RE.search(p)
    if dm:
        qty_raw = dm.group(1).lower()
        qty = _NUM_WORDS.get(qty_raw)
        if qty is None:
            try: qty = int(qty_raw)
            except ValueError: qty = 1
        unit = dm.group(2).lower()
        duration = f"{qty} {unit}{'s' if qty!=1 else ''}"

    interests: List[str] = []
    for kw, canon in INTEREST_KEYWORDS.items():
        if kw in p and canon not in interests:
            interests.append(canon)

    ctx = {
        "preferences": prompt,
        "destination": destination,
        "origin":      origin,
        "season":      season,
        "budget_eur":  budget,
        "duration":    duration,
        "interests":   interests,
    }

    _banner("STEP 1 · CONTEXT EXTRACTION")
    _row("raw prompt",    prompt, 300)
    _row("cities found",  cities)
    _row("origin",        origin)
    _row("destination",   destination)
    _row("season",        season)
    _row("budget_eur",    budget)
    _row("duration",      duration)
    _row("interests",     interests)
    _banner_end()
    return ctx


# ═══════════════════════════════════════════════════════════════════════════
# Agent discovery
# ═══════════════════════════════════════════════════════════════════════════

def discover_agents() -> Dict[str, Any]:
    _banner("STEP 3 · AGENT DISCOVERY  (reads /.well-known/agent.json)")
    agents: Dict[str, Any] = {}
    for name, url in [
        ("travel",   TRAVEL_AGENT_URL),
        ("flight",   FLIGHT_AGENT_URL),
        ("hotel",    HOTEL_AGENT_URL),
        ("activity", ACTIVITY_AGENT_URL),
        ("weather",  WEATHER_AGENT_URL),
    ]:
        try:
            headers = tracing.inject_trace_headers()
            card    = http_get(f"{name}-agent",
                               f"{url}/.well-known/agent.json",
                               timeout=5, headers=headers).json()
            agents[name] = {"url": url, "card": card}

            skills       = card.get("skills", [])
            skill_ids    = [s.get("id") for s in skills]
            req_tools    = []
            for s in skills:
                req_tools.extend(s.get("requires_mcp_tools", []))

            _row(f"  {name.upper()} ✓ discovered",
                 f"{card.get('name')} v{card.get('version')}")
            _row(f"    skills",          skill_ids)
            _row(f"    requires_mcp",    req_tools)
            _row(f"    mcp_provided_by", card.get("mcp_provided_by", "orchestrator"))
            _sep()
        except Exception as exc:
            logger.warning("│  %s ✗  FAILED: %s", name.upper(), exc)
            agents[name] = {"url": url, "error": str(exc)}
    _banner_end()
    return agents


# ═══════════════════════════════════════════════════════════════════════════
# RAG
# ═══════════════════════════════════════════════════════════════════════════

def call_rag(query: str) -> List[Dict[str, Any]]:
    _banner("STEP 2 · RAG SEARCH")
    _row("query", query, 300)
    headers = tracing.inject_trace_headers()
    tracer  = get_tracer()
    with tracer.start_as_current_span("rag_search"):
        try:
            resp    = http_post_json("rag-service",
                                     f"{RAG_BASE.rstrip('/')}/search",
                                     {"query": query}, timeout=20, headers=headers)
            results = resp.json().get("results", [])[:RAG_DOC_LIMIT]
            _row("docs returned", len(results))
            for i, doc in enumerate(results, 1):
                snippet = doc.get("content","")[:140].replace("\n"," ")
                _row(f"  doc[{i}]", snippet)
            _banner_end()
            return results
        except Exception as exc:
            logger.warning("│  RAG FAILED: %s", exc)
            _banner_end()
            return []


def build_rag_context(docs: List[Dict[str, Any]]) -> str:
    parts = []
    for i, d in enumerate(docs[:RAG_DOC_LIMIT], 1):
        parts.append(f"DOC {i}: {_trunc(d.get('content',''), RAG_DOC_CHARS)}")
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════════════
# ╔══════════════════════════════════════════════════════════╗
# ║        CENTRAL MCP GOVERNANCE LAYER                      ║
# ║                                                          ║
# ║  _resolve_mcp_for_agent()                                ║
# ║    reads agent card → requires_mcp_tools                 ║
# ║    looks up each tool in SKILL_MCP_MAP                   ║
# ║    calls the MCP server                                   ║
# ║    returns populated mcp_results dict                     ║
# ║                                                          ║
# ║  Agents NEVER call MCP directly.                         ║
# ║  All governance, rate-limiting, audit happen here.        ║
# ╚══════════════════════════════════════════════════════════╝
# ═══════════════════════════════════════════════════════════════════════════

def _mcp_tool_call(
    mcp_server: str,
    mcp_url:    str,
    tool:       str,
    arguments:  Dict[str, Any],
    session_id: str,
) -> Dict[str, Any]:
    """
    Low-level MCP tools/call.  Always called by the orchestrator governance
    layer — never by agents.
    """
    headers = {
        "Accept":         "application/json, text/event-stream",
        "Mcp-Session-Id": session_id,
        **tracing.inject_trace_headers(),
    }
    payload = {
        "jsonrpc": "2.0",
        "id":      str(uuid.uuid4()),
        "method":  "tools/call",
        "params": {
            "name":      tool,
            "arguments": arguments,
            "_meta":     {"sessionId": session_id},
        },
    }
    tracer = get_tracer()
    with tracer.start_as_current_span(f"mcp_{tool}"):
        try:
            resp    = http_post_json(f"{mcp_server}/{tool}", mcp_url,
                                     payload, timeout=25, headers=headers)
            result  = resp.json()
            # FastMCP wraps result in result.content[0].text
            content = result.get("result", result)
            if isinstance(content, dict) and "content" in content:
                for item in content["content"]:
                    if item.get("type") == "text":
                        try:
                            content = json.loads(item["text"])
                        except Exception:
                            content = item["text"]
                        break
            return content if isinstance(content, dict) else {"raw": content}
        except Exception as exc:
            logger.warning("│  MCP CALL FAILED  tool=%s  error=%s", tool, exc)
            return {"error": str(exc)}


def _build_mcp_arguments(tool: str, user_context: Dict[str, Any]) -> Dict[str, Any]:
    """Build MCP tool arguments from user context."""
    dest     = user_context.get("destination", "Unknown")
    origin   = user_context.get("origin",      "Unknown")
    season   = user_context.get("season")
    duration = user_context.get("duration")
    date     = SEASON_DATE.get(season or "", "2025-07-15")

    if tool == "search_flights":
        return {
            "origin":        origin,
            "destination":   dest,
            "date":          date,
            "trace_context": tracing.inject_trace_headers(),
        }
    if tool == "search_hotels":
        # crude checkout from duration
        try:
            days = int((duration or "7 days").split()[0]) * (
                7 if "week" in (duration or "") else 1)
        except Exception:
            days = 7
        checkout_day = int(date[8:]) + days
        checkout = date[:8] + str(checkout_day).zfill(2)
        return {
            "city":          dest,
            "checkin":       date,
            "checkout":      checkout,
            "trace_context": tracing.inject_trace_headers(),
        }
    if tool == "search_activities":
        return {
            "city":          dest,
            "trace_context": tracing.inject_trace_headers(),
        }
    if tool == "get_weather_forecast":
        return {
            "city":          dest,
            "date":          date,
            "trace_context": tracing.inject_trace_headers(),
        }
    return {}


def _resolve_mcp_for_agent(
    agent_name:   str,
    card:         Dict[str, Any],
    user_context: Dict[str, Any],
    session_id:   str,
    # cache: tools already called this pipeline (avoid duplicate calls)
    mcp_cache:    Dict[str, Any],
) -> Dict[str, Any]:
    """
    Governance entry point.

    1. Read agent card → skills → requires_mcp_tools
    2. For each required tool, look up SKILL_MCP_MAP
    3. Call the MCP server (or reuse cached result)
    4. Return mcp_results dict keyed by result_key
    """
    _banner(f"GOVERNANCE · MCP RESOLUTION FOR {agent_name.upper()}")
    _row("agent",           agent_name)
    _row("mcp_provided_by", card.get("mcp_provided_by", "orchestrator"))

    # Collect all required tools from all skills on this agent card
    required_tools: List[str] = []
    for skill in card.get("skills", []):
        skill_id     = skill.get("id", "?")
        skill_tools  = skill.get("requires_mcp_tools", [])
        _row(f"  skill [{skill_id}]  requires", skill_tools)
        for tool in skill_tools:
            if tool not in required_tools:
                required_tools.append(tool)

    if not required_tools:
        _row("  → no MCP tools required for this agent")
        _banner_end()
        return {}

    mcp_results: Dict[str, Any] = {}
    for tool_name in required_tools:
        reg = SKILL_MCP_MAP.get(tool_name)
        if not reg:
            logger.warning("│  ⚠ tool '%s' not in SKILL_MCP_MAP — skipping", tool_name)
            continue

        result_key = reg["result_key"]

        # Reuse cached result if this tool was already called this pipeline
        if result_key in mcp_cache:
            _row(f"  tool [{tool_name}]", f"→ CACHE HIT  key={result_key}")
            mcp_results[result_key] = mcp_cache[result_key]
            continue

        _sep()
        _row(f"  tool [{tool_name}]",
             f"→ {reg['mcp_server']} / {tool_name}")
        _row("    registry entry",
             f"server={reg['mcp_server']}  url={reg['mcp_url']}  result_key={result_key}")

        # Governance check — confirm mapping before calling
        logger.info("│")
        logger.info("│  ┌─ GOVERNANCE AUDIT ─────────────────────────────────────┐")
        logger.info("│  │  skill_source   : %s", agent_name)
        logger.info("│  │  required_tool  : %s", tool_name)
        logger.info("│  │  resolved_server: %s", reg["mcp_server"])
        logger.info("│  │  resolved_url   : %s", reg["mcp_url"])
        logger.info("│  │  result_key     : %s", result_key)
        logger.info("│  │  called_by      : orchestrator (NOT the agent)")
        logger.info("│  └────────────────────────────────────────────────────────┘")
        logger.info("│")

        arguments = _build_mcp_arguments(tool_name, user_context)
        _row("    arguments", arguments, 400)

        result = _mcp_tool_call(
            mcp_server = reg["mcp_server"],
            mcp_url    = reg["mcp_url"],
            tool       = tool_name,
            arguments  = arguments,
            session_id = session_id,
        )

        _row(f"    ← result [{result_key}]", result, 600)
        mcp_results[result_key] = result
        mcp_cache[result_key]   = result   # cache for other agents

    _banner_end()
    return mcp_results


# ═══════════════════════════════════════════════════════════════════════════
# A2A client
# ═══════════════════════════════════════════════════════════════════════════

def a2a_send_task(
    agent_name:  str,
    agent_url:   str,
    task_id:     str,
    session_id:  str,
    message:     A2AMessage,
    metadata:    Dict[str, Any] | None = None,
) -> Dict[str, Any]:

    headers = tracing.inject_trace_headers()
    headers["Content-Type"] = "application/json"
    rpc_request = build_tasks_send(task_id, session_id, message, metadata)

    _banner(f"STEP 6 · A2A SEND → {agent_name.upper()}")
    _row("jsonrpc",       "2.0")
    _row("method",        "tasks/send")
    _row("endpoint",      f"{agent_url}/a2a")
    _row("task_id",       task_id)
    _row("session_id",    session_id)
    if metadata:
        _row("metadata.skill",    metadata.get("skill"))
        _row("metadata.mcp_tools_used", metadata.get("mcp_tools_used"))

    for part in message.to_dict().get("parts", []):
        if part.get("type") == "data":
            d  = part["data"]
            uc = d.get("user_context", {})
            _sep()
            _row("  message.skill",       d.get("skill"))
            _row("  message.intent",      d.get("intent"))
            _row("  message.origin",      uc.get("origin"))
            _row("  message.destination", uc.get("destination"))
            _row("  message.budget_eur",  uc.get("budget_eur"))
            _row("  message.season",      uc.get("season"))
            _row("  message.interests",   uc.get("interests"))
            mr       = d.get("mcp_results", {})
            mcp_keys = list(mr.keys()) if isinstance(mr, dict) else []
            _row("  mcp_results keys",    mcp_keys)
            for k in mcp_keys:
                _row(f"    mcp_results.{k}", mr[k], 300)

    tracer = get_tracer()
    with tracer.start_as_current_span(f"a2a_{agent_name}_tasks_send"):
        resp        = http_post_json(f"{agent_name}-a2a",
                                     f"{agent_url}/a2a",
                                     rpc_request,
                                     timeout=OLLAMA_TIMEOUT, headers=headers)
        rpc_response = resp.json()

    _banner(f"STEP 6 · A2A RECV ← {agent_name.upper()}")

    if "error" in rpc_response:
        err = rpc_response["error"]
        logger.error("│  JSON-RPC ERROR  code=%s  msg=%s",
                     err.get("code"), err.get("message"))
        _banner_end()
        raise RuntimeError(f"A2A error {err.get('code')}: {err.get('message')}")

    task      = rpc_response.get("result", {})
    state     = task.get("status", {}).get("state", "?")
    sp        = task.get("status", {}).get("message", {}).get("parts", [])
    status_txt = sp[0].get("text","") if sp else ""
    artifacts  = task.get("artifacts", [])

    _row("task_id",    task.get("id"))
    _row("state",      state)
    _row("status_msg", status_txt, 200)
    _row("artifacts",  [a.get("name") for a in artifacts])
    _row("metadata",   task.get("metadata", {}), 300)
    _sep()

    for art in artifacts:
        art_name = art.get("name","?")
        for part in art.get("parts", []):
            if part.get("type") == "data":
                d = part["data"]
                _row(f"  [{art_name}] status",   d.get("status"))
                for r in (d.get("reasoning") or []):
                    _row(f"  [{art_name}] reasoning", r, 220)
                decision = d.get("decision")
                if isinstance(decision, dict):
                    for dk, dv in decision.items():
                        _row(f"  [{art_name}] decision.{dk}", dv, 350)
                elif decision is not None:
                    _row(f"  [{art_name}] decision", decision, 350)
                if d.get("a2a_request"):
                    _row(f"  [{art_name}] a2a_request", d["a2a_request"], 200)

    _banner_end()
    return task


# ═══════════════════════════════════════════════════════════════════════════
# Flask routes
# ═══════════════════════════════════════════════════════════════════════════

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "model": LLM_MODEL, "version": "0.4"}), 200


@app.route("/.well-known/agent.json", methods=["GET"])
def agent_card():
    card = build_agent_card(
        name="orchestrator",
        description=(
            "Central A2A orchestrator with MCP governance. "
            "Discovers agents, resolves their skill→MCP requirements through the "
            "central SKILL_MCP_MAP registry, calls all MCP tools on behalf of agents, "
            "then fans out enriched tasks via A2A tasks/send."
        ),
        url=f"{ORCHESTRATOR_URL}/a2a",
        version="3.3.0",
        skills=[{
            "id":          "plan_trip",
            "name":        "Plan Trip",
            "description": "End-to-end travel planning with central MCP governance.",
            "mcp_tools_owned": list(SKILL_MCP_MAP.keys()),
            "mcp_servers": [TRAVEL_MCP_URL, WEATHER_MCP_URL],
            "inputModes":  ["application/json"],
            "outputModes": ["application/json"],
            "tags":        ["travel", "orchestration", "governance"],
        }],
    )
    return jsonify(card), 200


@app.route("/a2a", methods=["POST"])
def a2a_endpoint():
    raw = request.get_json(force=True) or {}
    try:
        req_id, method, params = parse_jsonrpc(raw)
    except ValueError as exc:
        return jsonify(jsonrpc_error(ERR_INVALID_PARAMS, str(exc), raw.get("id"))), 400

    if method == "tasks/send":
        return _handle_tasks_send(req_id, params)
    if method in ("tasks/get", "tasks/cancel"):
        return jsonify(jsonrpc_error(-32001, "Stateless orchestrator", req_id)), 200
    return jsonify(jsonrpc_error(ERR_METHOD_NOT_FOUND,
                                  f"Unknown method: {method}", req_id)), 200


def _handle_tasks_send(req_id, params: Dict[str, Any]):
    tracer = get_tracer()
    with tracer.start_as_current_span("orchestrator_tasks_send"):
        task_id    = params.get("id")    or str(uuid.uuid4())
        session_id = params.get("sessionId") or str(uuid.uuid4())
        message    = params.get("message", {})
        prompt     = _extract_text_from_message(message)

        if not prompt:
            return jsonify(jsonrpc_error(ERR_INVALID_PARAMS,
                "message must contain a text part with the user prompt", req_id)), 400

        _box(f"ORCHESTRATOR v3.3.0  —  NEW TASK")
        logger.info("  task_id    : %s", task_id)
        logger.info("  session_id : %s", session_id)
        logger.info("  prompt     : %s", prompt)
        logger.info("  tokens     : %d", count_tokens(prompt))

        try:
            itinerary = _run_planning_pipeline(prompt, task_id, session_id)
        except Exception as exc:
            logger.exception("Planning pipeline failed")
            task = Task(
                id=task_id, session_id=session_id,
                status=TaskStatus(
                    state="failed",
                    message=A2AMessage.agent_text(f"Planning failed: {exc}").to_dict(),
                ),
                metadata={"error": str(exc)},
            )
            return jsonify(jsonrpc_success(task.to_dict(), req_id)), 200

        artifact = Artifact(
            name="itinerary",
            parts=[DataPart(itinerary).to_dict()],
        )
        task = Task(
            id=task_id, session_id=session_id,
            status=TaskStatus(
                state="completed",
                message=A2AMessage.agent_text(
                    f"Trip planned for {itinerary.get('destination','destination')}."
                ).to_dict(),
            ),
            artifacts=[artifact.to_dict()],
            metadata={"model": LLM_MODEL, "version": "3.3.0"},
        )
        return jsonify(jsonrpc_success(task.to_dict(), req_id)), 200


def _extract_text_from_message(message: Dict[str, Any]) -> str:
    for part in message.get("parts", []):
        if part.get("type") == "text":
            return part.get("text", "")
        if part.get("type") == "data":
            data = part.get("data", {})
            if isinstance(data, dict):
                return data.get("prompt", "")
    return ""


# ═══════════════════════════════════════════════════════════════════════════
# Core planning pipeline
# ═══════════════════════════════════════════════════════════════════════════

def _run_planning_pipeline(prompt: str, conversation_id: str, session_id: str) -> Dict[str, Any]:
    tracer = get_tracer()
    with tracer.start_as_current_span("plan_trip_pipeline"):

        # ── Step 1 · Context ─────────────────────────────────────────────
        user_context = extract_user_context(prompt)
        dest    = user_context.get("destination") or "Unknown"
        origin  = user_context.get("origin")      or "Unknown"
        season  = user_context.get("season")
        duration= user_context.get("duration")

        # ── Step 2 · RAG ──────────────────────────────────────────────────
        rag_docs    = call_rag(prompt)
        rag_context = build_rag_context(rag_docs)

        dest_lower = dest.lower()
        if dest_lower and rag_docs:
            if all(dest_lower not in d.get("content","").lower() for d in rag_docs):
                logger.warning("⚠ RAG docs don't mention '%s' — may be noise", dest)

        # ── Step 3 · Agent discovery ──────────────────────────────────────
        agents = discover_agents()

        # ── Step 4 · Governance registry dump ────────────────────────────
        _banner("STEP 4 · SKILL → MCP GOVERNANCE REGISTRY")
        logger.info("│")
        logger.info("│  %-28s %-20s %-22s %s",
                    "SKILL ID", "MCP SERVER", "TOOL", "RESULT KEY")
        logger.info("│  %s", "─" * 68)
        for skill_id, reg in SKILL_MCP_MAP.items():
            logger.info("│  %-28s %-20s %-22s %s",
                        skill_id, reg["mcp_server"], reg["tool"], reg["result_key"])
        logger.info("│")
        _row("TRAVEL_MCP_URL",  TRAVEL_MCP_URL)
        _row("WEATHER_MCP_URL", WEATHER_MCP_URL)
        _row("Agents own MCP?", "NO — orchestrator is the sole MCP gateway")
        _banner_end()

        # ── Step 5 · Dynamic MCP pre-fetch per agent ──────────────────────
        # mcp_cache avoids calling the same tool twice even if two agents
        # both require it (e.g. weather needed by activity + weather agents)
        _banner("STEP 5 · DYNAMIC MCP PRE-FETCH  (governance-driven)")
        _row("pattern", "orchestrator reads agent cards → resolves SKILL_MCP_MAP → calls MCP")
        _banner_end()

        mcp_cache: Dict[str, Any] = {}
        agent_mcp_results: Dict[str, Dict[str, Any]] = {}

        for name, info in agents.items():
            if info.get("error"):
                continue
            card = info.get("card", {})
            mcp_results = _resolve_mcp_for_agent(
                agent_name   = name,
                card         = card,
                user_context = user_context,
                session_id   = session_id,
                mcp_cache    = mcp_cache,
            )
            agent_mcp_results[name] = mcp_results

        # ── Step 5b · MCP cache summary ───────────────────────────────────
        _banner("STEP 5b · MCP CACHE SUMMARY  (all tools fetched this pipeline)")
        for key, val in mcp_cache.items():
            _row(f"  cached [{key}]", val, 400)
        _banner_end()

        # ── Step 6 · Intent summary ────────────────────────────────────────
        orchestrator_intent = {
            "origin":      origin,
            "destination": dest,
            "season":      season,
            "budget_eur":  user_context.get("budget_eur"),
            "duration":    duration,
            "interests":   user_context.get("interests"),
        }
        _banner("STEP 6 · ORCHESTRATOR INTENT SUMMARY")
        for k, v in orchestrator_intent.items():
            _row(k, v)
        _banner_end()

        # ── Step 7 · A2A fan-out ───────────────────────────────────────────
        _box("STEP 7  —  A2A FAN-OUT  (tasks/send to each agent)")
        loop_log: List[Dict[str, Any]] = []
        specialist_results: Dict[str, Any] = {}

        # Helper to dispatch one agent
        def _dispatch(agent_name: str, skill: str, intent: str,
                      extra_payload: Dict[str, Any]) -> None:
            if agent_name not in agents or agents[agent_name].get("error"):
                logger.warning("  %s skipped — not available", agent_name)
                return

            mcp_results  = agent_mcp_results.get(agent_name, {})
            mcp_keys_used = list(mcp_results.keys())

            msg = A2AMessage.user_data({
                "skill":        skill,
                "intent":       intent,
                "user_context": user_context,
                "rag_context":  rag_context,
                "payload":      extra_payload,
                "mcp_results":  mcp_results,
            })

            try:
                task = a2a_send_task(
                    agent_name, agents[agent_name]["url"],
                    task_id    = f"{conversation_id}-{agent_name}",
                    session_id = session_id,
                    message    = msg,
                    metadata   = {
                        "skill":          skill,
                        "mcp_tools_used": mcp_keys_used,
                        "mcp_governance": "orchestrator",
                    },
                )
                result_key = f"{agent_name}_result"
                specialist_results[agent_name] = (
                    get_artifact_data(task, result_key) or task
                )
                loop_log.append({
                    "agent":          agent_name,
                    "skill":          skill,
                    "mcp_tools_used": mcp_keys_used,
                    "state":          task.get("status", {}).get("state"),
                    "task_id":        task.get("id"),
                })
            except Exception as exc:
                logger.error("%s AGENT FAILED: %s", agent_name.upper(), exc)
                loop_log.append({"agent": agent_name, "state": "error",
                                 "error": str(exc)})

        _dispatch("flight",   "rank_flights",       "rank_flights",
                  {"prompt": prompt, "budget_eur": user_context.get("budget_eur"),
                   "origin": origin, "destination": dest, "season": season})

        _dispatch("hotel",    "rank_hotels",        "rank_hotels",
                  {"prompt": prompt, "budget_eur": user_context.get("budget_eur"),
                   "destination": dest, "duration": duration})

        _dispatch("activity", "suggest_activities", "suggest_activities",
                  {"prompt": prompt, "destination": dest,
                   "season": season, "interests": user_context.get("interests")})

        _dispatch("weather",  "get_forecast",       "get_forecast",
                  {"prompt": prompt, "destination": dest, "season": season})

        # ── Step 8 · Pipeline summary ──────────────────────────────────────
        _box("STEP 8  —  PIPELINE COMPLETE")
        _row("conversation_id", conversation_id)
        _row("rag_docs_used",   len(rag_docs))
        _row("mcp_tools_called", list(mcp_cache.keys()))
        logger.info("│")
        for entry in loop_log:
            icon = "✓" if entry.get("state") == "completed" else "✗"
            logger.info("│  %s  %-12s  skill=%-22s  mcp=%s  state=%s",
                        icon,
                        entry.get("agent","?"),
                        entry.get("skill","?"),
                        entry.get("mcp_tools_used","?"),
                        entry.get("state","?"))
        logger.info("│")

        return {
            "conversation_id":   conversation_id,
            "intent":            orchestrator_intent,
            "loop":              loop_log,
            "user_context":      user_context,
            "rag_docs_count":    len(rag_docs),
            "agents_discovered": list(agents.keys()),
            "mcp_data":          mcp_cache,
            "agent_results":     specialist_results,
            "origin":      origin,
            "destination": dest,
            "season":      season,
            "budget_eur":  user_context.get("budget_eur"),
            "duration":    duration,
        }


# ─── Legacy REST wrapper ─────────────────────────────────────────────────────

@app.route("/plan", methods=["POST"])
def plan_trip():
    tracer = get_tracer()
    with tracer.start_as_current_span("plan_trip_rest"):
        body   = request.get_json(force=True)
        prompt = body.get("prompt")
        if not prompt:
            return jsonify({"error": "prompt required"}), 400
        cid = str(uuid.uuid4())
        try:
            itinerary = _run_planning_pipeline(prompt, cid, cid)
        except Exception as exc:
            logger.exception("Pipeline failed")
            return jsonify({"error": str(exc)}), 500
        return jsonify(itinerary)


if __name__ == "__main__":
    logger.info("ORCHESTRATOR v3.3.0 STARTED  model=%s", LLM_MODEL)
    app.run(host="0.0.0.0", port=9000, debug=False)
