"""
Frontend Service v2.0 — A2A-aware, token-instrumented

Changes from v1:
  - POST / still renders index.html (original behaviour preserved)
  - New GET/POST /plan JSON API used by the updated index.html via fetch()
  - Posts to orchestrator /a2a (tasks/send) with fallback to /plan (REST)
  - Returns structured itinerary + per-agent token counts + wall-clock timing
"""

import json
import logging
import os
import time
import uuid

import requests
from flask import Flask, jsonify, render_template, request

from shared.logging import configure_logging
from shared.metrics import register_metrics
from shared.tracing import init_tracing, inject_trace_headers

# ──────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────

ORCHESTRATOR_URL     = os.getenv("ORCHESTRATOR_URL", "http://orchestrator:9000")
ORCHESTRATOR_TIMEOUT = float(os.getenv("ORCHESTRATOR_TIMEOUT", "120.0"))
LOG_LEVEL            = os.getenv("LOG_LEVEL", "INFO")
SERVICE              = "frontend"

app = Flask(__name__)
configure_logging(SERVICE)
meter = register_metrics(app, SERVICE)
init_tracing(app, SERVICE)

logger = logging.getLogger(SERVICE)
logger.info("ORCHESTRATOR_URL=%s", ORCHESTRATOR_URL)
logger.info("ORCHESTRATOR_TIMEOUT=%s", ORCHESTRATOR_TIMEOUT)

# ──────────────────────────────────────────────────────────────
# Token counting helpers
# ──────────────────────────────────────────────────────────────

def _count(text) -> int:
    """Rough word-split token estimate (matches orchestrator's count_tokens)."""
    if not text:
        return 0
    if isinstance(text, (dict, list)):
        try:
            text = json.dumps(text, ensure_ascii=False)
        except Exception:
            text = str(text)
    return len(str(text).split())


def _extract_agent_tokens(agent_result: dict, agent_name: str) -> dict:
    """
    Estimate token usage from an agent artifact result.
      input  ≈ system_prompt_overhead + intent + reasoning
      output ≈ decision + reasoning
    """
    if not isinstance(agent_result, dict):
        return {"agent": agent_name, "input": 0, "output": 0, "status": "unknown"}

    # Unwrap if we got a raw Task dict instead of the data payload
    data = agent_result
    for art in agent_result.get("artifacts", []):
        for part in art.get("parts", []):
            if part.get("type") == "data":
                data = part["data"]
                break

    SYSTEM_PROMPT_OVERHEAD = 120   # fixed per-agent system prompt word estimate

    reasoning_text = " ".join(data.get("reasoning", []) or [])
    decision_text  = data.get("decision") or ""
    intent_text    = data.get("intent") or ""

    input_tokens  = SYSTEM_PROMPT_OVERHEAD + _count(intent_text) + _count(reasoning_text)
    output_tokens = _count(decision_text) + _count(reasoning_text)

    return {
        "agent":  agent_name,
        "input":  input_tokens,
        "output": output_tokens,
        "status": data.get("status", "unknown"),
    }


def _build_token_report(prompt: str, rag_docs_count: int,
                        user_context: dict, agent_results: dict,
                        raw_response: dict) -> dict:
    """
    Full token accounting report.

    Tiers:
      user_input        — raw prompt words
      rag_augmentation  — words injected from RAG (≈55 words × doc count)
      context_injection — serialised user_context sent to every agent
      agents            — per-agent LLM input / output breakdown
      response          — serialised itinerary returned to the browser
    """
    user_tokens      = _count(prompt)
    rag_tokens       = rag_docs_count * 55
    ctx_tokens       = _count(user_context)
    augmented_tokens = user_tokens + rag_tokens + ctx_tokens

    agents_breakdown  = []
    total_agent_input = 0
    total_agent_output = 0

    for name, result in (agent_results or {}).items():
        row = _extract_agent_tokens(result, name)
        agents_breakdown.append(row)
        total_agent_input  += row["input"]
        total_agent_output += row["output"]

    response_tokens = _count(raw_response)

    return {
        "user_input":         user_tokens,
        "rag_augmentation":   rag_tokens,
        "context_injection":  ctx_tokens,
        "augmented_total":    augmented_tokens,
        "agents":             agents_breakdown,
        "total_agent_input":  total_agent_input,
        "total_agent_output": total_agent_output,
        "response":           response_tokens,
        "grand_total":        augmented_tokens + total_agent_input + total_agent_output + response_tokens,
    }


# ──────────────────────────────────────────────────────────────
# Orchestrator client
# ──────────────────────────────────────────────────────────────

def _call_orchestrator_a2a(prompt: str) -> dict:
    """Send via A2A tasks/send, extract and return the itinerary data dict."""
    task_id    = str(uuid.uuid4())
    session_id = str(uuid.uuid4())

    payload = {
        "jsonrpc": "2.0",
        "id":      task_id,
        "method":  "tasks/send",
        "params": {
            "id":        task_id,
            "sessionId": session_id,
            "message": {
                "role":  "user",
                "parts": [{"type": "text", "text": prompt}],
            },
        },
    }

    headers = {"Content-Type": "application/json", **inject_trace_headers()}
    resp = requests.post(
        f"{ORCHESTRATOR_URL.rstrip('/')}/a2a",
        json=payload,
        headers=headers,
        timeout=ORCHESTRATOR_TIMEOUT,
    )
    resp.raise_for_status()
    rpc = resp.json()

    if "error" in rpc:
        raise RuntimeError(
            f"Orchestrator A2A error {rpc['error']['code']}: {rpc['error']['message']}"
        )

    task = rpc.get("result", {})
    for art in task.get("artifacts", []):
        if art.get("name") == "itinerary":
            for part in art.get("parts", []):
                if part.get("type") == "data":
                    return part["data"]

    raise RuntimeError("No itinerary artifact in orchestrator response")


def _call_orchestrator_rest(prompt: str) -> dict:
    """Legacy /plan fallback."""
    headers = {"Content-Type": "application/json", **inject_trace_headers()}
    resp = requests.post(
        f"{ORCHESTRATOR_URL.rstrip('/')}/plan",
        json={"prompt": prompt},
        headers=headers,
        timeout=ORCHESTRATOR_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


# ──────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────

@app.route("/", methods=["GET", "POST"])
def home():
    """Original route — preserved exactly as before."""
    prompt      = ""
    ai_response = ""
    error       = ""

    if request.method == "POST":
        prompt = request.form.get("prompt", "").strip()
        if not prompt:
            error = "Prompt cannot be empty."
        else:
            logger.info("Sending user prompt to orchestrator", extra={"prompt_len": len(prompt)})
            try:
                resp = requests.post(
                    f"{ORCHESTRATOR_URL.rstrip('/')}/plan",
                    json={"prompt": prompt},
                    headers={"Content-Type": "application/json", **inject_trace_headers()},
                    timeout=ORCHESTRATOR_TIMEOUT,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    ai_response = data.get("text", data.get("response", ""))
                    if not ai_response:
                        # Summarise the structured itinerary as readable text
                        ai_response = _format_itinerary_text(data)
                else:
                    error = f"Orchestrator returned status {resp.status_code}: {resp.text}"
            except requests.RequestException as exc:
                logger.error("Failed to call orchestrator", exc_info=True)
                error = f"Communication error with orchestrator: {exc}"

    return render_template(
        "index.html",
        prompt=prompt,
        ai_response=ai_response,
        error=error,
    )


@app.route("/api/plan", methods=["POST"])
def api_plan():
    """
    JSON API consumed by the updated index.html via fetch().

    Request body:  { "prompt": "..." }
    Response:      { "itinerary": {...}, "tokens": {...}, "elapsed_ms": N, "transport": "a2a"|"rest" }
    """
    body   = request.get_json(force=True) or {}
    prompt = (body.get("prompt") or "").strip()

    if not prompt:
        return jsonify({"error": "Prompt cannot be empty."}), 400

    logger.info("API PLAN REQUEST  prompt=%s", prompt[:120])
    t0 = time.time()

    # Try A2A first, fall back to REST
    transport = "a2a"
    try:
        itinerary = _call_orchestrator_a2a(prompt)
    except Exception as exc:
        logger.warning("A2A call failed (%s), falling back to /plan REST", exc)
        transport = "rest"
        try:
            itinerary = _call_orchestrator_rest(prompt)
        except Exception as exc2:
            logger.error("Both orchestrator paths failed: %s", exc2)
            return jsonify({"error": str(exc2)}), 502

    elapsed_ms = int((time.time() - t0) * 1000)

    tokens = _build_token_report(
        prompt         = prompt,
        rag_docs_count = itinerary.get("rag_docs_count", 0),
        user_context   = itinerary.get("user_context", {}),
        agent_results  = itinerary.get("agent_results", {}),
        raw_response   = itinerary,
    )

    logger.info(
        "API PLAN COMPLETE  elapsed=%dms  transport=%s  grand_total_tokens=%d",
        elapsed_ms, transport, tokens["grand_total"],
    )

    return jsonify({
        "itinerary":  itinerary,
        "tokens":     tokens,
        "elapsed_ms": elapsed_ms,
        "transport":  transport,
    })


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/orchestrator-status")
def orchestrator_status():
    """Diagnostic endpoint — checks orchestrator connectivity."""
    reachable = False
    metadata  = {}
    try:
        resp      = requests.get(
            f"{ORCHESTRATOR_URL.rstrip('/')}/health", timeout=5.0
        )
        reachable = resp.status_code == 200
        metadata  = resp.json() if resp.content else {}
    except Exception as exc:
        logger.debug("Orchestrator health check failed: %s", exc)

    return jsonify({
        "orchestrator_url":       ORCHESTRATOR_URL,
        "orchestrator_reachable": reachable,
        "orchestrator_metadata":  metadata,
    })


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def _format_itinerary_text(data: dict) -> str:
    """
    Convert a structured itinerary dict into readable plain text for the
    original POST / → index.html template that expects a string in ai_response.
    """
    lines = []
    intent = data.get("intent") or data.get("user_context", {})
    if intent.get("destination"):
        lines.append(
            f"Trip to {intent['destination']}"
            + (f" from {intent['origin']}" if intent.get("origin") else "")
            + (f", {intent['season']}" if intent.get("season") else "")
            + (f", {intent['duration']}" if intent.get("duration") else "")
            + (f", budget €{intent['budget_eur']}" if intent.get("budget_eur") else "")
        )

    results = data.get("agent_results", {})

    flight_data = results.get("flight", {})
    flights = (flight_data.get("decision") or {}).get("ranked_flights", [])
    if flights:
        lines.append("\nFlights:")
        for f in flights[:3]:
            lines.append(
                f"  • {f.get('origin','?')} → {f.get('destination', f.get('arrival_airport','?'))}"
                + (f"  €{f.get('price','?')}" if f.get('price') else "")
            )

    hotel_data   = results.get("hotel", {})
    hotels       = hotel_data.get("ranked_hotels") or (hotel_data.get("decision") or {}).get("ranked_hotels", [])
    if hotels:
        lines.append("\nHotels:")
        for h in hotels[:3]:
            lines.append(
                f"  • {h.get('hotel_name', h.get('name','?'))}"
                + (f"  €{h.get('price','?')}/night" if h.get('price') else "")
            )

    activity_data = results.get("activity", {})
    activities    = (activity_data.get("decision") or {}).get("selected_activities", [])
    if activities:
        lines.append("\nActivities:")
        for a in activities[:5]:
            lines.append(f"  • {a.get('title','?')}")

    loop = data.get("loop", [])
    if loop:
        states = ", ".join(f"{e['agent']}:{e.get('state','?')}" for e in loop)
        lines.append(f"\nAgents: {states}")

    return "\n".join(lines) if lines else str(data)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
