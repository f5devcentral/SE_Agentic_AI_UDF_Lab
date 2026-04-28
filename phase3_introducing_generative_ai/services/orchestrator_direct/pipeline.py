"""
orchestrator-direct/pipeline.py
─────────────────────────────────
Non-agentic planning pipeline.

Steps 1-6 are IDENTICAL to the agentic orchestrator:
  1  context extraction
  2  RAG search
  3  (skipped — no agent discovery)
  4  MCP governance registry log
  5  MCP pre-fetch  (all four tools: flights, hotels, activities, weather)
  5b MCP cache summary
  6  intent summary

Step 7 replaces the A2A fan-out with a SINGLE Ollama LLM call.
The LLM receives all MCP data + RAG context in one prompt and produces
a structured itinerary JSON in one shot.

Step 8 pipeline summary — identical shape to agentic version.

Token accounting is logged:
  • after Step 2  (prompt + RAG tokens visible early)
  • before Step 7 (full input budget — prompt + RAG + all MCP data)
  • after Step 7  (output tokens from Ollama eval_count if available)

This lets the demo audience directly compare token consumption vs the
agentic version where every agent gets the full context independently.
"""

import json
import logging
import time
from typing import Any, Dict, List, Optional

import requests

import log_utils as L
import shared.tracing as tracing
from config import (
    LLM_MAX_TOKENS, LLM_MODEL, LLM_NUM_CTX,
    OLLAMA_TIMEOUT, OLLAMA_URL,
    SEASON_DATE, SKILL_MCP_MAP, TRAVEL_MCP_URL, WEATHER_MCP_URL,
    DIRECT_AGENT_CARD,
)
from context_extractor import extract_user_context
from rag_client import call_rag, build_rag_context
from mcp_gateway import resolve_mcp_for_agent, clear_session_cache
from guardrail import (
    scan_user_prompt, scan_rag_context, scan_mcp_data, scan_llm_response,
    guardrail_blocked_message, F5GUARDRAILS_ENABLED,
)

logger = logging.getLogger("orchestrator-direct")


# ═════════════════════════════════════════════════════════════════════════════
# LLM prompt templates
# ═════════════════════════════════════════════════════════════════════════════

_SYSTEM_PROMPT = """\
You are TravelMind, an expert travel planner.
You will receive:
  - the user's travel request
  - relevant destination knowledge (RAG context)
  - live flight options (from MCP)
  - live hotel options (from MCP)
  - live activities (from MCP)
  - a weather forecast (from MCP)

Return ONLY a valid JSON object — no markdown fences, no explanation.

Required schema:
{
  "destination": "<city>",
  "origin": "<city>",
  "season": "<season>",
  "budget_eur": <number or null>,
  "duration": "<e.g. 1 week>",

  "flight": {
    "airline": "<name>",
    "departure_time": "<HH:MM>",
    "arrival_time": "<HH:MM>",
    "duration_hours": <number>,
    "price": <number>,
    "stops": <number>,
    "reasoning": "<one sentence why this flight was chosen>"
  },

  "hotel": {
    "name": "<hotel name>",
    "stars": <number>,
    "rating": <number>,
    "price_per_night": <number>,
    "total_price_7n": <number>,
    "amenities": ["<amenity>"],
    "reasoning": "<one sentence why this hotel was chosen>"
  },

  "activities": [
    {
      "title": "<activity name>",
      "weather_suitable": <true|false>,
      "why_selected": "<one sentence>"
    }
  ],

  "weather_summary": "<2 sentences on weather and packing tips>",

  "budget_check": {
    "flight_cost": <number>,
    "hotel_cost_7n": <number>,
    "total_cost": <number>,
    "within_budget": <true|false>,
    "note": "<one sentence>"
  },

  "overall_recommendation": "<2-3 sentence trip summary>"
}

Rules:
- Choose the BEST flight (balance price vs duration vs stops)
- Choose the BEST hotel (balance rating vs price vs amenities vs user interests)
- Include ALL activities from the MCP data
- weather_suitable = false only for outdoor activities with rain/storm forecast
- All string values must be plain text — no special characters that break JSON
- Output MUST be complete valid JSON — do not truncate under any circumstance
"""


def _build_user_prompt(
    user_context: Dict[str, Any],
    rag_context:  str,
    mcp_cache:    Dict[str, Any],
) -> str:
    """Assemble the single user-turn prompt sent to Ollama."""
    flights    = mcp_cache.get("flights",    [])
    hotels     = mcp_cache.get("hotels",     [])
    activities = mcp_cache.get("activities", [])
    weather    = mcp_cache.get("weather",    {})

    # Compress weather to avoid ballooning the prompt
    weather_days = [
        f"{d['date']} {d['condition']} high={d['temp_high_c']}°C "
        f"precip={d['precipitation_mm']}mm"
        for d in weather.get("days", [])[:7]
    ]

    parts = [
        "=== USER REQUEST ===",
        user_context.get("preferences", ""),
        "",
        "=== DESTINATION KNOWLEDGE (RAG) ===",
        rag_context or "(none)",
        "",
        "=== LIVE FLIGHTS (MCP) ===",
        json.dumps(flights, indent=None),
        "",
        "=== LIVE HOTELS (MCP) ===",
        json.dumps(hotels, indent=None),
        "",
        "=== LIVE ACTIVITIES (MCP) ===",
        json.dumps(activities, indent=None),
        "",
        "=== WEATHER FORECAST (MCP) ===",
        json.dumps(weather_days, indent=None),
        "",
        "Now produce the JSON itinerary.",
    ]
    return "\n".join(parts)


# ═════════════════════════════════════════════════════════════════════════════
# Ollama call
# ═════════════════════════════════════════════════════════════════════════════

def _call_ollama(system: str, user: str) -> Dict[str, Any]:
    """
    Call Ollama /api/generate and return a dict with:
      raw_text, output_tokens, total_duration_s
    """
    payload = {
        "model":  LLM_MODEL,
        "prompt": f"<|system|>\n{system}\n<|user|>\n{user}\n<|assistant|>\n",
        "stream": False,
        "options": {
            "num_predict": LLM_MAX_TOKENS,
            "num_ctx":     LLM_NUM_CTX,
            "temperature": 0.1,
            "stop":        [],
        },
    }

    url = f"{OLLAMA_URL}/api/generate"
    L.banner("STEP 7 · LLM CALL  (single Ollama request)")
    L.row("url",            url)
    L.row("model",          LLM_MODEL)
    L.row("num_predict",    LLM_MAX_TOKENS)
    L.row("num_ctx",        LLM_NUM_CTX)
    L.row("prompt_bytes",   len(payload["prompt"].encode()))
    L.banner_end()

    t0   = time.monotonic()
    resp = requests.post(url, json=payload, timeout=OLLAMA_TIMEOUT)
    resp.raise_for_status()
    elapsed = time.monotonic() - t0

    data = resp.json()
    raw  = data.get("response", "")

    # Ollama returns token counts in eval_count / prompt_eval_count
    output_tokens = data.get("eval_count", L.count_tokens(raw))
    input_tokens  = data.get("prompt_eval_count", 0)
    duration_s    = round(data.get("total_duration", elapsed * 1e9) / 1e9, 2)

    L.banner("STEP 7 · LLM RESPONSE")
    L.row("elapsed_s",      f"{elapsed:.2f}s")
    L.row("total_duration", f"{duration_s}s  (Ollama wall clock)")
    L.row("input_tokens",   f"{input_tokens:,}  (Ollama prompt_eval_count)")
    L.row("output_tokens",  f"{output_tokens:,}  (Ollama eval_count)")
    L.row("tokens_per_sec", f"{round(output_tokens / max(elapsed, 0.01)):,}")
    L.row("raw_length",     f"{len(raw)} chars")
    L.row("raw_preview",    raw[:400])
    L.banner_end()

    return {
        "raw_text":       raw,
        "output_tokens":  output_tokens,
        "input_tokens":   input_tokens,
        "total_duration": duration_s,
    }


def _safe_parse(raw: str) -> Dict[str, Any]:
    """
    Three-stage JSON extraction:
      1) direct parse after stripping markdown fences
      2) extract first { ... } block
      3) attempt truncation repair (close open strings/braces)
    """
    import re

    cleaned = re.sub(r"```(?:json)?", "", raw).strip().strip("`").strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    # repair truncated output
    s = cleaned
    in_str = esc = False
    for ch in s:
        if esc:
            esc = False
            continue
        if ch == "\\" and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
    if in_str:
        s += '"'
    s += "]" * max(0, s.count("[") - s.count("]"))
    s += "}" * max(0, s.count("{") - s.count("}"))
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass

    raise ValueError(f"Could not parse LLM output as JSON: {raw[:300]!r}")


# ═════════════════════════════════════════════════════════════════════════════
# Public entry point
# ═════════════════════════════════════════════════════════════════════════════

def run_planning_pipeline(
    prompt:          str,
    conversation_id: str,
    session_id:      str,
) -> Dict[str, Any]:
    """
    Non-agentic planning pipeline.
    Same signature and return shape as the agentic orchestrator's
    run_planning_pipeline() so app.py needs zero changes.
    """
    tracer = tracing.get_tracer("orchestrator-direct")

    with tracer.start_as_current_span("plan_trip_pipeline_direct"):

        clear_session_cache()

        # ── Guardrail SCAN POINT 1 · user prompt ─────────────────────────────
        g_prompt = scan_user_prompt(prompt)
        if not g_prompt["allowed"]:
            logger.warning("[GUARDRAIL] User prompt FLAGGED — aborting pipeline")
            return _guardrail_abort(conversation_id, "user prompt",
                                    g_prompt["blocked_reason"])

        # ── Step 1 · Context extraction ───────────────────────────────────────
        user_context = extract_user_context(prompt)
        dest     = user_context.get("destination") or "Unknown"
        origin   = user_context.get("origin")      or "Unknown"
        season   = user_context.get("season")
        duration = user_context.get("duration")
        budget   = user_context.get("budget_eur")

        prompt_tokens = L.count_tokens(prompt)
        # logged inside extract_user_context via the shared log_utils banner

        # ── Step 2 · RAG ──────────────────────────────────────────────────────
        rag_docs    = call_rag(prompt)
        rag_context = build_rag_context(rag_docs)
        rag_tokens  = L.count_tokens(rag_context)

        # ── Guardrail SCAN POINT 2 · RAG retrieved context ───────────────────
        g_rag = scan_rag_context(rag_context)
        if not g_rag["allowed"]:
            logger.warning("[GUARDRAIL] RAG context FLAGGED — aborting pipeline")
            return _guardrail_abort(conversation_id, "RAG context",
                                    g_rag["blocked_reason"])

        # ── Token accounting snapshot: prompt + RAG (visible early) ──────────
        L.log_token_accounting(
            label         = "after RAG  (pre-MCP)",
            prompt_tokens = prompt_tokens,
            rag_tokens    = rag_tokens,
            mcp_tokens    = 0,
            total_tokens  = prompt_tokens + rag_tokens,
            max_tokens    = LLM_MAX_TOKENS,
        )

        # ── Step 3 · (skipped — no agent discovery in direct mode) ───────────
        L.banner("STEP 3 · AGENT DISCOVERY  (skipped — direct mode, no agents)")
        L.row("mode",   "direct")
        L.row("reason", "single LLM call replaces all specialist agents")
        L.banner_end()

        # ── Step 4 · Governance registry ──────────────────────────────────────
        _log_governance_registry()

        # ── Step 5 · MCP pre-fetch (same governance logic as agentic) ─────────
        L.banner("STEP 5 · DYNAMIC MCP PRE-FETCH  (governance-driven)")
        L.row("pattern",
              "orchestrator reads DIRECT_AGENT_CARD → resolves SKILL_MCP_MAP → calls MCP")
        L.banner_end()

        mcp_cache: Dict[str, Any] = {}

        # Reuse the exact same resolve_mcp_for_agent() from mcp_gateway —
        # we pass the synthetic DIRECT_AGENT_CARD which declares all four tools.
        resolve_mcp_for_agent(
            agent_name   = "direct-llm",
            card         = DIRECT_AGENT_CARD,
            user_context = user_context,
            session_id   = session_id,
            mcp_cache    = mcp_cache,
        )

        # ── Step 5b · MCP cache summary ───────────────────────────────────────
        L.banner("STEP 5b · MCP CACHE SUMMARY")
        mcp_tokens = 0
        if mcp_cache:
            for key, val in mcp_cache.items():
                serialised  = json.dumps(val, ensure_ascii=False)
                key_tokens  = L.count_tokens(serialised)
                mcp_tokens += key_tokens
                L.row(f"  cached [{key}]",  val, 400)
                L.row(f"  tokens [{key}]",  f"{key_tokens:,}")
        else:
            L.row("  (empty — no MCP tools were resolved)")
        L.banner_end()

        # ── Guardrail SCAN POINT 3 · MCP tool results ───────────────────────
        g_mcp = scan_mcp_data(mcp_cache)
        if not g_mcp["allowed"]:
            logger.warning("[GUARDRAIL] MCP data FLAGGED — aborting pipeline")
            return _guardrail_abort(conversation_id, "MCP data",
                                    g_mcp["blocked_reason"])

        # ── Step 6 · Intent summary ───────────────────────────────────────────
        orchestrator_intent = {
            "origin":      origin,
            "destination": dest,
            "season":      season,
            "budget_eur":  budget,
            "duration":    duration,
            "interests":   user_context.get("interests"),
        }
        L.banner("STEP 6 · ORCHESTRATOR INTENT SUMMARY")
        for k, v in orchestrator_intent.items():
            L.row(k, v)
        L.banner_end()

        # ── Full token accounting before LLM call ─────────────────────────────
        # Build the prompt now so we can count it accurately
        user_prompt   = _build_user_prompt(user_context, rag_context, mcp_cache)
        system_tokens = L.count_tokens(_SYSTEM_PROMPT)
        user_tokens   = L.count_tokens(user_prompt)
        total_input   = system_tokens + user_tokens

        # Pre-call: log the input budget breakdown (output not known yet)
        L.banner("TOKEN ACCOUNTING  ·  input budget (pre-LLM)")
        L.row("system_prompt_tokens",    f"{system_tokens:,}")
        L.row("user_prompt_tokens",      f"{user_tokens:,}")
        L.row("  of which: raw_prompt",  f"{prompt_tokens:,}")
        L.row("  of which: rag_context", f"{rag_tokens:,}")
        L.row("  of which: mcp_data",    f"{mcp_tokens:,}")
        L.row("total_input_tokens",      f"{total_input:,}")
        L.row("max_output_tokens",       f"{LLM_MAX_TOKENS:,}")
        L.row("context_window",          f"{LLM_NUM_CTX:,}")
        L.row("input_utilisation",
              f"{round(total_input / LLM_NUM_CTX * 100, 1)}%  of context window used by input")
        L.banner_end()

        # ── Step 7 · Single LLM call ──────────────────────────────────────────
        L.box("STEP 7  —  DIRECT LLM CALL  (no agents)")
        llm_out      = _call_ollama(_SYSTEM_PROMPT, user_prompt)
        raw_llm_text = llm_out["raw_text"]

        # ── Guardrail SCAN POINT 4 · LLM response ────────────────────────────
        g_output = scan_llm_response(raw_llm_text)
        if not g_output["allowed"]:
            logger.warning("[GUARDRAIL] LLM response FLAGGED — blocking")
            return _guardrail_abort(conversation_id, "LLM response",
                                    g_output["blocked_reason"],
                                    intent=orchestrator_intent)

        # parse
        itinerary_data: Dict[str, Any] = {}
        parse_error: Optional[str]     = None
        try:
            itinerary_data = _safe_parse(raw_llm_text)
            L.banner("STEP 7 · PARSE SUCCESS")
            L.row("keys_found", list(itinerary_data.keys()))
            L.banner_end()
        except ValueError as exc:
            parse_error = str(exc)
            logger.error("LLM output parse failed: %s", parse_error)
            L.banner("STEP 7 · PARSE FAILED  (returning raw)")
            L.row("error", parse_error)
            L.banner_end()
            itinerary_data = {"raw_llm_output": raw_llm_text,
                              "parse_error":    parse_error}

        # ── Token accounting COMPLETE  (post-LLM — all numbers now known) ─────
        # Ollama reports prompt_eval_count (actual input tokens as tokenised by
        # the model) and eval_count (actual output tokens generated).  These are
        # more accurate than our word-split estimates and should be preferred.
        _ollama_input    = llm_out.get("input_tokens",  total_input)
        _ollama_output   = llm_out.get("output_tokens", 0)
        _ollama_total    = _ollama_input + _ollama_output
        _input_pct       = round(_ollama_input  / max(LLM_NUM_CTX, 1) * 100, 1)
        _output_pct      = round(_ollama_output / max(LLM_NUM_CTX, 1) * 100, 1)
        _total_pct       = round(_ollama_total  / max(LLM_NUM_CTX, 1) * 100, 1)

        L.banner("TOKEN ACCOUNTING  ·  COMPLETE  (post-LLM)")
        L.row("─── INPUT ───────────────────────",   "")
        L.row("  system_prompt_tokens",  f"{system_tokens:,}  (word-split estimate)")
        L.row("  user_prompt_tokens",    f"{user_tokens:,}  (word-split estimate)")
        L.row("    of which: raw_prompt",   f"{prompt_tokens:,}")
        L.row("    of which: rag_context",  f"{rag_tokens:,}")
        L.row("    of which: mcp_data",     f"{mcp_tokens:,}")
        L.row("  total_input (estimated)",  f"{total_input:,}  tokens")
        L.row("  total_input (Ollama)",     f"{_ollama_input:,}  tokens  ← authoritative")
        L.row("─── OUTPUT ──────────────────────",   "")
        L.row("  output_tokens (Ollama)",   f"{_ollama_output:,}  tokens  ← authoritative")
        L.row("  output_chars",             f"{len(raw_llm_text):,}  chars")
        L.row("─── TOTALS ──────────────────────",   "")
        L.row("  total_tokens_consumed",    f"{_ollama_total:,}  tokens  (input + output)")
        L.row("  context_window",           f"{LLM_NUM_CTX:,}  tokens")
        L.row("  input_utilisation",        f"{_input_pct}%  of context window")
        L.row("  output_utilisation",       f"{_output_pct}%  of context window")
        L.row("  total_utilisation",        f"{_total_pct}%  of context window")
        L.row("  max_output_tokens",        f"{LLM_MAX_TOKENS:,}  (configured cap)")
        L.row("  model",                    LLM_MODEL)
        L.banner_end()

        # ── Step 8 · Pipeline summary ─────────────────────────────────────────
        L.box("STEP 8  —  PIPELINE COMPLETE  (direct mode)")
        L.row("conversation_id",  conversation_id)
        L.row("rag_docs_used",    len(rag_docs))
        L.row("mcp_tools_called", list(mcp_cache.keys()))
        L.row("llm_model",        LLM_MODEL)
        L.row("input_tokens",     f"{_ollama_input:,}  (Ollama authoritative)")
        L.row("output_tokens",    f"{_ollama_output:,}  (Ollama authoritative)")
        L.row("total_tokens",     f"{_ollama_total:,}")
        L.row("context_pct",      f"{_total_pct}%  of {LLM_NUM_CTX:,}-token window used")
        L.row("parse_ok",         "yes" if not parse_error else f"NO — {parse_error[:80]}")
        logger.info("│")

        return {
            "conversation_id":    conversation_id,
            "mode":               "direct",
            "intent":             orchestrator_intent,
            "user_context":       user_context,
            "rag_docs_count":     len(rag_docs),
            "mcp_data":           mcp_cache,
            "agents_discovered":  [],          # no agents in direct mode
            "loop":               [],          # no agent loop entries
            "agent_results": {
                # mirror the agentic shape so the frontend can render both modes
                "flight":   _wrap_flight(itinerary_data),
                "hotel":    _wrap_hotel(itinerary_data),
                "activity": _wrap_activities(itinerary_data),
                "weather":  _wrap_weather(itinerary_data),
            },
            "origin":             origin,
            "destination":        dest,
            "season":             season,
            "budget_eur":         budget,
            "duration":           duration,
            "best_combo":         _extract_budget_check(itinerary_data),
            "over_budget_warning": _over_budget_warning(itinerary_data, budget),
            "budget_iterations":  1,
            # token accounting — surfaced for frontend / logviz
            "token_accounting": {
                # input breakdown (word-split estimates)
                "prompt_tokens":        prompt_tokens,
                "rag_tokens":           rag_tokens,
                "mcp_tokens":           mcp_tokens,
                "system_tokens":        system_tokens,
                "user_tokens":          user_tokens,
                "total_input_estimated":total_input,
                # authoritative counts from Ollama
                "input_tokens":         _ollama_input,
                "output_tokens":        _ollama_output,
                "total_tokens":         _ollama_total,
                # context window utilisation
                "context_window":       LLM_NUM_CTX,
                "max_output_tokens":    LLM_MAX_TOKENS,
                "input_pct":            _input_pct,
                "output_pct":           _output_pct,
                "total_pct":            _total_pct,
                "model":                LLM_MODEL,
            },
            # raw itinerary for any consumer that wants the full struct
            "itinerary":          itinerary_data,
        }


# ═════════════════════════════════════════════════════════════════════════════
# Shape adapters — map direct LLM output → agentic agent_results shape
# so the frontend rendering code is identical for both modes.
# ═════════════════════════════════════════════════════════════════════════════

def _wrap_flight(data: Dict) -> Dict:
    f = data.get("flight", {})
    if not f:
        return {}
    return {
        "status": "completed",
        "decision": {
            "ranked_flights":  [f],
            "best_choice":     1,
            "recommendation":  f.get("reasoning", ""),
        },
        "reasoning": [f.get("reasoning", "")],
    }


def _wrap_hotel(data: Dict) -> Dict:
    h = data.get("hotel", {})
    if not h:
        return {}
    return {
        "status":          "completed",
        "recommendation":  h.get("name", ""),
        "ranked_hotels": [{
            "hotel_name":         h.get("name", ""),
            "stars":              h.get("stars", 0),
            "rating":             h.get("rating", 0),
            "price_per_night_eur":h.get("price_per_night", 0),
            "score":              0.9,
            "within_budget":      True,
            "why_ranked":         h.get("reasoning", ""),
        }],
        "new_intents": [],
    }


def _wrap_activities(data: Dict) -> Dict:
    acts = data.get("activities", [])
    return {
        "status": "completed",
        "agent":  "direct-llm",
        "decision": {
            "recommendation":    data.get("overall_recommendation", ""),
            "selected_activities": [
                {
                    "title":            a.get("title", ""),
                    "fit_score":        0.8,
                    "weather_suitable": a.get("weather_suitable", True),
                    "why_selected":     a.get("why_selected", ""),
                }
                for a in acts
            ],
        },
        "reasoning": ["Single LLM call — no weather agent"],
    }


def _wrap_weather(data: Dict) -> Dict:
    return {
        "status":          "completed",
        "summary":         data.get("weather_summary", ""),
        "good_days":       [],
        "bad_days":        [],
        "packing_tips":    [],
        "activity_impact": "",
    }


def _extract_budget_check(data: Dict) -> Optional[Dict]:
    bc = data.get("budget_check", {})
    if not bc:
        return None
    return {
        "flight_cost": bc.get("flight_cost"),
        "hotel_cost":  bc.get("hotel_cost_7n"),
        "total":       bc.get("total_cost"),
        "gap":         0,
        "date":        SEASON_DATE.get("spring", ""),
        "iteration":   1,
        "flight_data": data.get("flight"),
        "hotel_data":  data.get("hotel"),
    }


def _over_budget_warning(data: Dict, budget: Optional[float]) -> Optional[str]:
    bc = data.get("budget_check", {})
    if not bc or not budget:
        return None
    total = bc.get("total_cost", 0)
    if total and total > budget:
        return (
            f"⚠️  The best combination found costs €{total:.0f}, "
            f"which is €{total - budget:.0f} over your €{budget:.0f} budget."
        )
    return None


# ═════════════════════════════════════════════════════════════════════════════
# Governance registry log (identical to agentic pipeline)
# ═════════════════════════════════════════════════════════════════════════════

def _log_governance_registry():
    L.banner("STEP 4 · SKILL → MCP GOVERNANCE REGISTRY  (keyed by tool name)")
    logger.info("│")
    logger.info("│  %-28s %-20s %-12s",
                "TOOL NAME (requires_mcp_tools)", "MCP SERVER", "RESULT KEY")
    logger.info("│  %s", "─" * 65)
    for tool_name, reg in SKILL_MCP_MAP.items():
        logger.info("│  %-28s %-20s %-12s",
                    tool_name, reg["mcp_server"], reg["result_key"])
    logger.info("│")
    L.row("TRAVEL_MCP_URL",  TRAVEL_MCP_URL)
    L.row("WEATHER_MCP_URL", WEATHER_MCP_URL)
    L.row("Registry keyed by",
          "MCP TOOL NAME — same registry as agentic orchestrator")
    L.row("Agents own MCP?", "NO — orchestrator is the sole MCP gateway")
    L.banner_end()


# ═════════════════════════════════════════════════════════════════════════════
# Guardrail abort helper
# ═════════════════════════════════════════════════════════════════════════════

def _guardrail_abort(conversation_id: str, scan_point: str,
                     blocked_reason: str, intent: Dict[str, Any] = None) -> Dict[str, Any]:
    """Standard early-return dict when any guardrail scan flags content."""
    return {
        "conversation_id":   conversation_id,
        "mode":              "direct",
        "guardrail_blocked": True,
        "guardrail_outcome": "flagged",
        "guardrail_reason":  blocked_reason,
        "guardrail_stage":   scan_point,
        "response":          guardrail_blocked_message(blocked_reason, scan_point),
        "intent":            intent or {},
        "agent_results":     {},
        "mcp_data":          {},
        "token_accounting":  {},
        "itinerary":         {},
    }
