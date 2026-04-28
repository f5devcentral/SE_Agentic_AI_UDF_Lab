"""
services/flights-agent/app.py  —  A2A Flight Agent  v3.3.0
═══════════════════════════════════════════════════════════

Governance pattern:
  • NO MCP URLs — agent never calls MCP directly
  • Agent card declares requires_mcp_tools: ["search_flights"]
    so the orchestrator knows what data to fetch and inject
  • mcp_results.flights arrives pre-populated in the A2A task message
  • Agent role: receive data → reason with LLM → return ranked decision
"""

import json
import logging
import os
from typing import Any, Dict, List

import requests
from flask import Flask, jsonify, request

from shared.a2a_protocol import (
    A2AMessage, Artifact, DataPart,
    ERR_INVALID_PARAMS, ERR_METHOD_NOT_FOUND,
    Task, TaskStatus,
    build_agent_card, jsonrpc_error, jsonrpc_success, parse_jsonrpc,
)
from shared.logging import configure_logging
from shared.metrics import register_metrics
from shared.tracing import get_tracer, inject_trace_headers, init_tracing

# ═══════════════════════════════════════════════════════════════════
# Config  —  NO TRAVEL_MCP_URL here (governance: orchestrator only)
# ═══════════════════════════════════════════════════════════════════

SERVICE_NAME     = "flight-agent"
AGENT_PORT       = int(os.getenv("AGENT_PORT",       "8002"))
AGENT_URL        = os.getenv("AGENT_URL",             f"http://flight-agent:{AGENT_PORT}")

OLLAMA_URL        = os.getenv("OLLAMA_URL",           "http://ollama:11434")
LLM_MODEL         = os.getenv("LLM_MODEL",            "llama3.2:1b")
OLLAMA_TIMEOUT    = int(os.getenv("OLLAMA_TIMEOUT",   "90"))
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE",    "30m")
OLLAMA_NUM_PREDICT= int(os.getenv("OLLAMA_NUM_PREDICT","256"))

app = Flask(__name__)
configure_logging(SERVICE_NAME)
meter = register_metrics(app, SERVICE_NAME)
init_tracing(app, SERVICE_NAME)

logger = logging.getLogger(SERVICE_NAME)
logger.setLevel(logging.INFO)

W = 68

def _banner(title):
    logger.info("┌─ %s %s", title, "─" * max(0, W - len(title) - 4))

def _banner_end():
    logger.info("└%s", "─" * (W - 1))

def _row(label, value=None, limit=800):
    if value is None:
        logger.info("│  %s", label)
    else:
        s = _trunc(value, limit)
        logger.info("│  %-30s %s", label + ":", s)

def _sep():
    logger.info("│  %s", "·" * (W - 4))

def _trunc(v, limit=800):
    if v is None: return "None"
    if isinstance(v, (dict, list)):
        try: v = json.dumps(v, ensure_ascii=False)
        except: v = str(v)
    else: v = str(v)
    return v if len(v) <= limit else v[:limit] + f"… [{len(v)-limit} chars]"

# ═══════════════════════════════════════════════════════════════════
# LLM
# ═══════════════════════════════════════════════════════════════════

def ollama_chat(system_prompt: str, user_prompt: str) -> str:
    headers = inject_trace_headers()
    tracer  = get_tracer(SERVICE_NAME)
    with tracer.start_as_current_span("ollama_chat") as span:
        span.set_attribute("llm.model", LLM_MODEL)
        span.set_attribute("llm.input_tokens_est", len(user_prompt.split()))
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user",   "content": user_prompt},
                ],
                "stream":     False,
                "format":     "json",
                "keep_alive": OLLAMA_KEEP_ALIVE,
                "options":    {"num_predict": OLLAMA_NUM_PREDICT, "temperature": 0.2},
            },
            headers=headers,
            timeout=OLLAMA_TIMEOUT,
        )
        resp.raise_for_status()
        content = resp.json()["message"]["content"]
        span.set_attribute("llm.output_tokens_est", len(content.split()))
        return content

# ═══════════════════════════════════════════════════════════════════
# Core reasoning
# ═══════════════════════════════════════════════════════════════════

def _run_flight_reasoning(task_id: str, task_payload: Dict[str, Any]) -> Dict[str, Any]:
    skill        = task_payload.get("skill",  "rank_flights")
    intent       = task_payload.get("intent", "rank_flights")
    user_context = task_payload.get("user_context", {})
    payload      = task_payload.get("payload", {})
    mcp_results  = task_payload.get("mcp_results", {})
    rag_context  = task_payload.get("rag_context", "")

    # Real flights injected by orchestrator governance layer
    flights: List[Dict] = []
    raw = mcp_results.get("flights", {})
    if isinstance(raw, dict):
        flights = raw.get("flights", raw.get("results", []))
    elif isinstance(raw, list):
        flights = raw

    _banner("FLIGHT AGENT · TASK RECEIVED")
    _row("task_id",          task_id)
    _row("skill",            skill)
    _row("intent",           intent)
    _row("mcp_provided_by",  "orchestrator (governance pattern)")
    _row("flights from MCP", len(flights))
    _row("rag_context chars",len(str(rag_context)))
    _sep()
    _row("user_context.origin",      user_context.get("origin"))
    _row("user_context.destination", user_context.get("destination"))
    _row("user_context.season",      user_context.get("season"))
    _row("user_context.budget_eur",  user_context.get("budget_eur"))
    _row("user_context.interests",   user_context.get("interests"))
    _sep()
    for i, f in enumerate(flights, 1):
        _row(f"  flight[{i}]", f, 300)
    _banner_end()

    system_prompt = """You are flight-agent in an A2A multi-agent travel system.

GOVERNANCE MODEL: You do NOT call MCP tools. The orchestrator called
travel-mcp/search_flights on your behalf and injected the results below.
Your role is to REASON over the provided flight data and rank the options.

Return ONLY valid JSON:
{
  "intent": {"origin": "...", "destination": "...", "budget_eur": 0},
  "reasoning": [
    "Step 1: examined N flights from MCP data",
    "Step 2: filtered by budget constraint",
    "Step 3: ranked by price/duration tradeoff"
  ],
  "decision": {
    "ranked_flights": [
      {
        "flight_id": 1,
        "airline": "...",
        "origin": "...",
        "destination": "...",
        "departure": "...",
        "arrival": "...",
        "duration": "...",
        "price_eur": 0,
        "class": "Economy",
        "why_ranked": "best price within budget"
      }
    ],
    "best_choice": 1,
    "recommendation": "..."
  },
  "status": "completed",
  "a2a_request": null
}

Use RAG context only as destination background knowledge, not as a flight data source.
"""
    user_prompt = json.dumps({
        "skill":             skill,
        "task":              intent,
        "user_context":      user_context,
        "flights_from_mcp":  flights,
        "rag_context_chars": len(str(rag_context)),
        "payload":           payload,
    })

    _banner("FLIGHT AGENT · LLM REASONING")
    _row("model",             LLM_MODEL)
    _row("input flights",     len(flights))
    _row("system_prompt len", len(system_prompt))
    _row("user_prompt len",   len(user_prompt))
    _banner_end()

    try:
        raw_out  = ollama_chat(system_prompt, user_prompt)
        decision = json.loads(raw_out)

        _banner("FLIGHT AGENT · LLM DECISION")
        _row("status",    decision.get("status"))
        for r in (decision.get("reasoning") or []):
            _row("  reasoning", r, 220)
        ranked = (decision.get("decision") or {}).get("ranked_flights", [])
        _row("ranked flights", len(ranked))
        for i, f in enumerate(ranked, 1):
            _row(f"  [{i}] {f.get('airline','?')}",
                 f"€{f.get('price_eur','?')}  {f.get('duration','?')}  → {f.get('why_ranked','?')}", 200)
        _row("recommendation",
             (decision.get("decision") or {}).get("recommendation",""), 200)
        _banner_end()

    except Exception as exc:
        logger.exception("flight-agent LLM failure")
        decision = {
            "intent": {
                "origin":      user_context.get("origin"),
                "destination": user_context.get("destination"),
                "budget_eur":  user_context.get("budget_eur"),
            },
            "reasoning":   [f"LLM error: {exc}",
                            f"MCP provided {len(flights)} flights"],
            "decision":    {"ranked_flights": flights[:3], "best_choice": 1,
                            "recommendation": "Fallback — LLM error"},
            "status":      "error",
            "a2a_request": None,
        }
        _banner("FLIGHT AGENT · FALLBACK DECISION")
        _row("error", str(exc))
        _banner_end()

    return decision

# ═══════════════════════════════════════════════════════════════════
# Routes
# ═══════════════════════════════════════════════════════════════════

@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/.well-known/agent.json", methods=["GET"])
def agent_card():
    card = build_agent_card(
        name        = "flight-agent",
        description = (
            "LLM flight specialist. Declares required MCP tools so the "
            "orchestrator governance layer fetches data on its behalf. "
            "Agent only reasons — never calls MCP directly."
        ),
        url     = f"{AGENT_URL}/a2a",
        version = "3.3.0",
        skills  = [{
            "id":                "rank_flights",
            "name":              "Rank Flights",
            "description":       "Rank and recommend flights using LLM reasoning over MCP data.",
            "requires_mcp_tools":["search_flights"],
            "mcp_provided_by":   "orchestrator",
            "inputModes":        ["application/json"],
            "outputModes":       ["application/json"],
            "tags":              ["flights", "travel"],
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
        return jsonify(jsonrpc_error(-32001, "Stateless agent", req_id)), 200
    return jsonify(jsonrpc_error(ERR_METHOD_NOT_FOUND,
                                  f"Unknown method: {method}", req_id)), 200


def _handle_tasks_send(req_id, params: Dict[str, Any]):
    tracer = get_tracer(SERVICE_NAME)
    with tracer.start_as_current_span("flight_agent_tasks_send") as span:
        task_id      = params.get("id", "unknown")
        session_id   = params.get("sessionId", "unknown")
        task_payload = _extract_data_part(params.get("message", {})) or {}
        skill        = task_payload.get("skill", "rank_flights")

        span.set_attribute("task.id",  task_id)
        span.set_attribute("skill",    skill)
        span.set_attribute("agent",    SERVICE_NAME)

        decision = _run_flight_reasoning(task_id, task_payload)
        state    = "completed" if decision.get("status") != "error" else "failed"

        task = Task(
            id=task_id, session_id=session_id,
            status=TaskStatus(
                state=state,
                message=A2AMessage.agent_text(
                    f"[{SERVICE_NAME}] skill={skill}  status={decision.get('status')}"
                ).to_dict(),
            ),
            artifacts=[Artifact(
                name  ="flight_result",
                parts =[DataPart(decision).to_dict()],
            ).to_dict()],
            metadata={"agent": SERVICE_NAME, "model": LLM_MODEL,
                      "skill": skill, "mcp_provided_by": "orchestrator"},
        )
        return jsonify(jsonrpc_success(task.to_dict(), req_id)), 200


def _extract_data_part(message: Dict[str, Any]) -> Dict[str, Any] | None:
    for part in message.get("parts", []):
        if part.get("type") == "data":
            return part.get("data")
    return None


# Legacy shims
@app.route("/reason", methods=["POST"])
def reason_compat():
    data = request.get_json(force=True) or {}
    return _handle_tasks_send("legacy", {
        "id": data.get("task_id","legacy"), "sessionId": "legacy",
        "message": {"role":"user","parts":[{"type":"data","data":data}]},
    })

@app.route("/a2a/message", methods=["POST"])
def a2a_message_compat():
    data    = request.get_json(force=True) or {}
    task_id = data.get("task_id","compat-"+os.urandom(4).hex())
    return _handle_tasks_send("legacy", {
        "id": task_id, "sessionId": data.get("conversation_id", task_id),
        "message": {"role":"user","parts":[{"type":"data","data":data}]},
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=AGENT_PORT, debug=False)
