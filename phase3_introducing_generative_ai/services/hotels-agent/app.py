"""
services/hotels-agent/app.py  —  A2A Hotel Agent  v3.3.0
═════════════════════════════════════════════════════════

Governance pattern:
  • NO MCP URLs — agent never calls MCP directly
  • Declares requires_mcp_tools: ["search_hotels"]
  • mcp_results.hotels pre-populated by orchestrator
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
from shared.tracing import init_tracing, inject_trace_headers
import shared.tracing as _tracing

SERVICE_NAME     = "hotel-agent"
AGENT_PORT       = int(os.getenv("AGENT_PORT",       "8003"))
AGENT_URL        = os.getenv("AGENT_URL",             f"http://hotel-agent:{AGENT_PORT}")
OLLAMA_URL       = os.getenv("OLLAMA_URL",            "http://ollama:11434")
LLM_MODEL        = os.getenv("LLM_MODEL",             "llama3.2:1b")
OLLAMA_TIMEOUT   = int(os.getenv("OLLAMA_TIMEOUT",    "120"))

app = Flask(__name__)
configure_logging(SERVICE_NAME)
meter = register_metrics(app, SERVICE_NAME)
init_tracing(app, SERVICE_NAME)
logger = logging.getLogger(SERVICE_NAME)
logger.setLevel(logging.INFO)
tracer = _tracing.get_tracer(SERVICE_NAME)

W = 68

def _banner(t):
    logger.info("┌─ %s %s", t, "─"*max(0,W-len(t)-4))
def _banner_end():
    logger.info("└%s","─"*(W-1))
def _row(label,value=None,limit=800):
    if value is None: logger.info("│  %s",label)
    else: logger.info("│  %-30s %s", label+":", _trunc(value,limit))
def _sep(): logger.info("│  %s","·"*(W-4))
def _trunc(v,limit=800):
    if v is None: return "None"
    if isinstance(v,(dict,list)):
        try: v=json.dumps(v,ensure_ascii=False)
        except: v=str(v)
    else: v=str(v)
    return v if len(v)<=limit else v[:limit]+f"… [{len(v)-limit} chars]"


def ollama_chat(system_prompt: str, user_prompt: str) -> str:
    headers = inject_trace_headers()
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
                "stream": False, "format": "json",
            },
            headers=headers, timeout=OLLAMA_TIMEOUT,
        )
        resp.raise_for_status()
        content = resp.json()["message"]["content"]
        span.set_attribute("llm.output_tokens_est", len(content.split()))
        return content


def _run_hotel_reasoning(task_id: str, task_payload: Dict[str, Any]) -> Dict[str, Any]:
    skill        = task_payload.get("skill",  "rank_hotels")
    intent       = task_payload.get("intent", "rank_hotels")
    user_context = task_payload.get("user_context", {})
    payload      = task_payload.get("payload", {})
    mcp_results  = task_payload.get("mcp_results", {})
    rag_context  = task_payload.get("rag_context", "")

    hotels: List[Dict] = []
    raw = mcp_results.get("hotels", {})
    if isinstance(raw, dict):
        hotels = raw.get("hotels", raw.get("results", []))
    elif isinstance(raw, list):
        hotels = raw

    budget      = user_context.get("budget_eur") or payload.get("budget_eur")
    preferences = user_context.get("preferences")

    _banner("HOTEL AGENT · TASK RECEIVED")
    _row("task_id",          task_id)
    _row("skill",            skill)
    _row("intent",           intent)
    _row("mcp_provided_by",  "orchestrator (governance pattern)")
    _row("hotels from MCP",  len(hotels))
    _row("budget_eur",       budget)
    _sep()
    for i, h in enumerate(hotels, 1):
        _row(f"  hotel[{i}]", h, 300)
    _banner_end()

    with tracer.start_as_current_span("hotel_agent_reason") as span:
        span.set_attribute("skill",        skill)
        span.set_attribute("hotels.count", len(hotels))
        span.set_attribute("has_budget",   budget is not None)

        system_prompt = """You are hotel-agent in an A2A multi-agent travel system.

GOVERNANCE MODEL: You do NOT call MCP tools. The orchestrator called
travel-mcp/search_hotels on your behalf and injected the results below.
Your role is to REASON over the provided hotel data and rank the options.

Return ONLY valid JSON:
{
  "ranked_hotels": [
    {
      "hotel_name": "...",
      "location": "...",
      "stars": 4,
      "price_per_night_eur": 0,
      "rating": 0.0,
      "score": 0.0,
      "within_budget": true,
      "why_ranked": "..."
    }
  ],
  "recommendation": "...",
  "new_intents": [],
  "status": "completed",
  "a2a_request": null
}

Use RAG context only as destination background knowledge.
"""
        user_prompt = json.dumps({
            "skill":           skill,
            "task":            intent,
            "user_context":    user_context,
            "hotels_from_mcp": hotels,
            "budget_eur":      budget,
            "preferences":     preferences,
            "rag_context_chars": len(str(rag_context)),
        })

        _banner("HOTEL AGENT · LLM REASONING")
        _row("model",           LLM_MODEL)
        _row("hotels provided", len(hotels))
        _row("budget",          budget)
        _banner_end()

        try:
            llm_raw  = ollama_chat(system_prompt, user_prompt)
            decision = json.loads(llm_raw)
            status   = decision.get("status","completed")
            span.set_attribute("status",              status)
            span.set_attribute("ranked_hotels.count", len(decision.get("ranked_hotels",[])))

            _banner("HOTEL AGENT · LLM DECISION")
            _row("status", status)
            for h in decision.get("ranked_hotels",[]):
                _row(f"  {h.get('hotel_name','?')}",
                     f"€{h.get('price_per_night_eur','?')}/night  "
                     f"★{h.get('rating','?')}  within_budget={h.get('within_budget')}  "
                     f"→ {h.get('why_ranked','?')}", 200)
            _row("recommendation", decision.get("recommendation",""), 200)
            _banner_end()

        except json.JSONDecodeError:
            logger.warning("hotel-agent JSON decode failed, fallback")
            span.set_status("error","JSON decode failed")
            decision = {
                "ranked_hotels":  hotels[:3],
                "recommendation": "Fallback — LLM not valid JSON",
                "new_intents":    [],
                "status":         "error",
            }
        return decision


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok"}), 200


@app.route("/.well-known/agent.json", methods=["GET"])
def agent_card():
    return jsonify(build_agent_card(
        name        = "hotel-agent",
        description = ("LLM hotel specialist. Declares required MCP tools so the "
                       "orchestrator fetches data on its behalf. Never calls MCP directly."),
        url     = f"{AGENT_URL}/a2a",
        version = "3.3.0",
        skills  = [{
            "id":                 "rank_hotels",
            "name":               "Rank Hotels",
            "description":        "Rank hotels from MCP data using LLM reasoning.",
            "requires_mcp_tools": ["search_hotels"],
            "mcp_provided_by":    "orchestrator",
            "inputModes":         ["application/json"],
            "outputModes":        ["application/json"],
            "tags":               ["hotels","travel"],
        }],
    )), 200


@app.route("/a2a", methods=["POST"])
def a2a_endpoint():
    raw = request.get_json(force=True) or {}
    try:
        req_id, method, params = parse_jsonrpc(raw)
    except ValueError as exc:
        return jsonify(jsonrpc_error(ERR_INVALID_PARAMS, str(exc), raw.get("id"))), 400
    if method == "tasks/send":
        return _handle_tasks_send(req_id, params)
    if method in ("tasks/get","tasks/cancel"):
        return jsonify(jsonrpc_error(-32001,"Stateless agent",req_id)), 200
    return jsonify(jsonrpc_error(ERR_METHOD_NOT_FOUND,
                                  f"Unknown method: {method}",req_id)), 200


def _handle_tasks_send(req_id, params):
    with tracer.start_as_current_span("hotel_agent_tasks_send") as span:
        task_id      = params.get("id","unknown")
        session_id   = params.get("sessionId","unknown")
        task_payload = _extract_data_part(params.get("message",{})) or {}
        skill        = task_payload.get("skill","rank_hotels")
        span.set_attribute("task.id",task_id)
        span.set_attribute("skill",  skill)

        decision = _run_hotel_reasoning(task_id, task_payload)
        state    = "completed" if decision.get("status") not in ("error",) else "failed"

        task = Task(
            id=task_id, session_id=session_id,
            status=TaskStatus(
                state=state,
                message=A2AMessage.agent_text(
                    f"[{SERVICE_NAME}] skill={skill}  status={decision.get('status')}"
                ).to_dict(),
            ),
            artifacts=[Artifact(
                name="hotel_result",
                parts=[DataPart(decision).to_dict()],
            ).to_dict()],
            metadata={"agent":SERVICE_NAME,"model":LLM_MODEL,
                      "skill":skill,"mcp_provided_by":"orchestrator"},
        )
        return jsonify(jsonrpc_success(task.to_dict(), req_id)), 200


def _extract_data_part(message):
    for part in message.get("parts",[]):
        if part.get("type")=="data":
            return part.get("data")
    return None


@app.route("/reason", methods=["POST"])
def reason():
    data = request.get_json(force=True) or {}
    return _handle_tasks_send("legacy",{
        "id":"legacy-"+os.urandom(4).hex(),"sessionId":"legacy",
        "message":{"role":"user","parts":[{"type":"data","data":data}]},
    })

@app.route("/a2a/message", methods=["POST"])
def a2a_message_compat():
    data    = request.get_json(force=True) or {}
    task_id = data.get("task_id","compat-"+os.urandom(4).hex())
    return _handle_tasks_send("legacy",{
        "id":task_id,"sessionId":data.get("conversation_id",task_id),
        "message":{"role":"user","parts":[{"type":"data","data":data}]},
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=AGENT_PORT, debug=False)
