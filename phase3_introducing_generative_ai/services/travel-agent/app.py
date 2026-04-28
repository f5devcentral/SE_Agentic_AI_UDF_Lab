"""
services/travel-agent/app.py  —  A2A Travel Agent  v3.3.0
══════════════════════════════════════════════════════════

Updated to true A2A pattern (matching flights-agent v3.3.0):

  BEFORE (mock)                   AFTER (real A2A)
  ─────────────────────────────   ──────────────────────────────────────
  /.well-known/agent-card         /.well-known/agent.json  (A2A spec)
  /a2a/message → {"received"}     /a2a  →  JSON-RPC 2.0 tasks/send
  module-level tracer import      get_tracer() pattern
  no skill / MCP metadata         requires_mcp_tools + mcp_provided_by
  flat custom JSON payload        A2AMessage data parts + Artifact result

Governance pattern:
  • NO MCP URLs — agent never calls MCP directly
  • Declares requires_mcp_tools: [] (travel-agent is a high-level planner;
    it orchestrates sub-intents rather than consuming raw MCP data itself)
  • Receives enriched context from the orchestrator and proposes
    next_intents for specialist agents
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
# Config  —  NO MCP URLs (governance: orchestrator only)
# ═══════════════════════════════════════════════════════════════════

SERVICE_NAME     = "travel-agent"
AGENT_PORT       = int(os.getenv("AGENT_PORT",       "8001"))
AGENT_URL        = os.getenv("AGENT_URL",             f"http://travel-agent:{AGENT_PORT}")

OLLAMA_URL        = os.getenv("OLLAMA_URL",           "http://ollama:11434")
LLM_MODEL         = os.getenv("LLM_MODEL",            "llama3.2:1b")
OLLAMA_TIMEOUT    = int(os.getenv("OLLAMA_TIMEOUT",   "120"))
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE",    "30m")
OLLAMA_NUM_PREDICT= int(os.getenv("OLLAMA_NUM_PREDICT","512"))

# ═══════════════════════════════════════════════════════════════════
# App bootstrap
# ═══════════════════════════════════════════════════════════════════

app = Flask(__name__)
configure_logging(SERVICE_NAME)
meter = register_metrics(app, SERVICE_NAME)
init_tracing(app, SERVICE_NAME)

logger = logging.getLogger(SERVICE_NAME)
logger.setLevel(logging.INFO)

W = 68

def _banner(title: str):
    logger.info("┌─ %s %s", title, "─" * max(0, W - len(title) - 4))

def _banner_end():
    logger.info("└%s", "─" * (W - 1))

def _row(label: str, value: Any = None, limit: int = 800):
    if value is None:
        logger.info("│  %s", label)
    else:
        logger.info("│  %-30s %s", label + ":", _trunc(value, limit))

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
        span.set_attribute("llm.model",                LLM_MODEL)
        span.set_attribute("llm.system_prompt.length", len(system_prompt))
        span.set_attribute("llm.user_prompt.length",   len(user_prompt))
        span.set_attribute("llm.input_tokens_est",     len(user_prompt.split()))
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
        span.set_attribute("llm.response.length",      len(content))
        span.set_attribute("llm.output_tokens_est",    len(content.split()))
        return content

# ═══════════════════════════════════════════════════════════════════
# Core reasoning
# ═══════════════════════════════════════════════════════════════════

def _run_travel_reasoning(task_id: str, task_payload: Dict[str, Any]) -> Dict[str, Any]:
    skill        = task_payload.get("skill",  "plan_trip")
    intent       = task_payload.get("intent", "plan_trip")
    user_context = task_payload.get("user_context", {})
    payload      = task_payload.get("payload", {})
    mcp_results  = task_payload.get("mcp_results", {})
    rag_context  = task_payload.get("rag_context", "")

    # Backwards compat with old /reason callers that sent prompt + context
    prompt   = payload.get("prompt") or user_context.get("preferences", "")
    context  = payload.get("context", {})
    rag_docs = payload.get("rag_docs", [])

    _banner("TRAVEL AGENT · TASK RECEIVED")
    _row("task_id",         task_id)
    _row("skill",           skill)
    _row("intent",          intent)
    _row("mcp_provided_by", "orchestrator (governance pattern)")
    _row("mcp_keys",        list(mcp_results.keys()) if mcp_results else "none")
    _sep()
    _row("user_context.origin",      user_context.get("origin"))
    _row("user_context.destination", user_context.get("destination"))
    _row("user_context.season",      user_context.get("season"))
    _row("user_context.budget_eur",  user_context.get("budget_eur"))
    _row("user_context.duration",    user_context.get("duration"))
    _row("user_context.interests",   user_context.get("interests"))
    _row("prompt",                   prompt, 300)
    _row("rag_docs count",           len(rag_docs))
    _banner_end()

    tracer = get_tracer(SERVICE_NAME)
    with tracer.start_as_current_span("travel_reasoning") as span:
        span.set_attribute("skill",           skill)
        span.set_attribute("task.id",         task_id)
        span.set_attribute("prompt.length",   len(prompt))
        span.set_attribute("rag_docs.count",  len(rag_docs))

        system_prompt = """You are travel-agent, a senior travel planner in an A2A multi-agent system.

GOVERNANCE MODEL: You do NOT call MCP tools. The orchestrator manages all
MCP calls on behalf of specialist agents. Your role is to:
1) Understand the user's goal and constraints from the context provided.
2) Propose sub-intents for specialist agents: flight-agent, hotel-agent,
   activity-agent, weather-agent.
3) For each intent, specify the target agent, intent name, and payload.

Return ONLY valid JSON:
{
  "intent": {
    "origin": "...",
    "destination": "...",
    "season": "...",
    "budget_eur": 0,
    "duration": "...",
    "interests": [...]
  },
  "reasoning": [
    "Step 1: identified destination and travel period",
    "Step 2: noted budget constraint",
    "Step 3: proposed specialist agent tasks"
  ],
  "summary": "brief natural language summary of the travel plan",
  "next_intents": [
    {
      "agent":   "flight-agent",
      "intent":  "rank_flights",
      "payload": {"origin": "...", "destination": "...", "budget_eur": 0}
    },
    {
      "agent":   "hotel-agent",
      "intent":  "rank_hotels",
      "payload": {"destination": "...", "budget_eur": 0, "duration": "..."}
    },
    {
      "agent":   "activity-agent",
      "intent":  "suggest_activities",
      "payload": {"destination": "...", "season": "...", "interests": [...]}
    },
    {
      "agent":   "weather-agent",
      "intent":  "get_forecast",
      "payload": {"destination": "...", "season": "..."}
    }
  ],
  "status": "completed"
}
"""
        user_prompt = json.dumps({
            "skill":        skill,
            "task":         intent,
            "user_context": user_context,
            "prompt":       prompt,
            "context":      context,
            "rag_docs":     rag_docs[:3],
        }, ensure_ascii=False)

        _banner("TRAVEL AGENT · LLM REASONING")
        _row("model",             LLM_MODEL)
        _row("system_prompt len", len(system_prompt))
        _row("user_prompt len",   len(user_prompt))
        _banner_end()

        try:
            raw_out  = ollama_chat(system_prompt, user_prompt)
            decision = json.loads(raw_out)

            _banner("TRAVEL AGENT · LLM DECISION")
            _row("status",  decision.get("status"))
            _row("summary", decision.get("summary",""), 200)
            for r in (decision.get("reasoning") or []):
                _row("  reasoning", r, 220)
            for ni in (decision.get("next_intents") or []):
                _row(f"  → {ni.get('agent','?')}",
                     f"intent={ni.get('intent','?')}  payload={_trunc(ni.get('payload',{}),120)}")
            span.set_attribute("next_intents.count",
                               len(decision.get("next_intents", [])))
            _banner_end()

        except Exception as exc:
            logger.exception("travel-agent LLM failure")
            decision = {
                "intent":   {
                    "origin":      user_context.get("origin"),
                    "destination": user_context.get("destination"),
                    "season":      user_context.get("season"),
                    "budget_eur":  user_context.get("budget_eur"),
                    "duration":    user_context.get("duration"),
                    "interests":   user_context.get("interests", []),
                },
                "reasoning":    [f"LLM error: {exc}"],
                "summary":      f"Fallback plan for {user_context.get('destination','destination')}",
                "next_intents": [
                    {"agent": "flight-agent",   "intent": "rank_flights",
                     "payload": {"origin": user_context.get("origin"),
                                 "destination": user_context.get("destination"),
                                 "budget_eur": user_context.get("budget_eur")}},
                    {"agent": "hotel-agent",    "intent": "rank_hotels",
                     "payload": {"destination": user_context.get("destination"),
                                 "budget_eur": user_context.get("budget_eur"),
                                 "duration": user_context.get("duration")}},
                    {"agent": "activity-agent", "intent": "suggest_activities",
                     "payload": {"destination": user_context.get("destination"),
                                 "season": user_context.get("season"),
                                 "interests": user_context.get("interests", [])}},
                    {"agent": "weather-agent",  "intent": "get_forecast",
                     "payload": {"destination": user_context.get("destination"),
                                 "season": user_context.get("season")}},
                ],
                "status": "error",
            }
            _banner("TRAVEL AGENT · FALLBACK DECISION")
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
        name        = "travel-agent",
        description = (
            "High-level LLM travel planner. Understands user intent and proposes "
            "sub-tasks for specialist agents (flight, hotel, activity, weather). "
            "Does not call MCP directly — orchestrator governs all MCP access."
        ),
        url     = f"{AGENT_URL}/a2a",
        version = "3.3.0",
        skills  = [{
            "id":                 "plan_trip",
            "name":               "Plan Trip",
            "description":        (
                "Understand the user travel request and decompose it into "
                "specialist agent tasks (flights, hotels, activities, weather)."
            ),
            "requires_mcp_tools": [],          # planner role — no direct MCP
            "mcp_provided_by":    "orchestrator",
            "inputModes":         ["application/json"],
            "outputModes":        ["application/json"],
            "tags":               ["travel", "planning", "orchestration"],
            "examples":           [
                "Plan a 1-week trip from Paris to Barcelona in spring under €1500"
            ],
        }],
    )
    return jsonify(card), 200


@app.route("/a2a", methods=["POST"])
def a2a_endpoint():
    """JSON-RPC 2.0 A2A endpoint — tasks/send, tasks/get, tasks/cancel."""
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
    with tracer.start_as_current_span("travel_agent_tasks_send") as span:
        task_id      = params.get("id", "unknown")
        session_id   = params.get("sessionId", "unknown")
        task_payload = _extract_data_part(params.get("message", {})) or {}
        skill        = task_payload.get("skill", "plan_trip")

        span.set_attribute("task.id",  task_id)
        span.set_attribute("skill",    skill)
        span.set_attribute("agent",    SERVICE_NAME)

        decision = _run_travel_reasoning(task_id, task_payload)
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
                name  = "travel_plan",
                parts = [DataPart(decision).to_dict()],
            ).to_dict()],
            metadata={
                "agent":          SERVICE_NAME,
                "model":          LLM_MODEL,
                "skill":          skill,
                "mcp_provided_by":"orchestrator",
            },
        )
        return jsonify(jsonrpc_success(task.to_dict(), req_id)), 200


def _extract_data_part(message: Dict[str, Any]) -> Dict[str, Any] | None:
    for part in message.get("parts", []):
        if part.get("type") == "data":
            return part.get("data")
    return None


# ═══════════════════════════════════════════════════════════════════
# Legacy compat routes  (kept so nothing breaks during rollout)
# ═══════════════════════════════════════════════════════════════════

@app.route("/.well-known/agent-card", methods=["GET"])
def agent_card_legacy():
    """Old path — redirect callers to the new A2A-spec path."""
    return agent_card()


@app.route("/reason", methods=["POST"])
def reason():
    """
    Legacy endpoint.  Old callers sent:
      { conversation_id, prompt, context: { rag_docs, ... } }
    We wrap that into a tasks/send call so the same reasoning path is used.
    """
    data            = request.get_json(force=True) or {}
    conversation_id = data.get("conversation_id", "legacy-" + os.urandom(4).hex())
    prompt          = data.get("prompt", "")
    context         = data.get("context", {})
    rag_docs        = context.get("rag_docs", [])

    fake_payload = {
        "skill":        "plan_trip",
        "intent":       "plan_trip",
        "user_context": context,
        "payload": {
            "prompt":   prompt,
            "context":  {k: v for k, v in context.items() if k != "rag_docs"},
            "rag_docs": rag_docs,
        },
        "mcp_results": {},
        "rag_context": "",
    }
    return _handle_tasks_send("legacy", {
        "id":        conversation_id,
        "sessionId": conversation_id,
        "message":   {"role": "user", "parts": [{"type": "data", "data": fake_payload}]},
    })


@app.route("/a2a/message", methods=["POST"])
def a2a_message_compat():
    """Old A2A stub — now routes through the real tasks/send handler."""
    data    = request.get_json(force=True) or {}
    task_id = data.get("task_id", "compat-" + os.urandom(4).hex())
    return _handle_tasks_send("legacy", {
        "id":        task_id,
        "sessionId": data.get("conversation_id", task_id),
        "message":   {"role": "user", "parts": [{"type": "data", "data": data}]},
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=AGENT_PORT, debug=False)
