"""
services/activities-agent/app.py  —  A2A Activity Agent  v3.3.0
════════════════════════════════════════════════════════════════

Governance pattern:
  • NO MCP URLs — agent never calls MCP directly
  • Declares requires_mcp_tools: ["search_activities", "get_weather_forecast"]
    → demonstrates one agent needing tools from TWO different MCP servers
    → orchestrator resolves both through SKILL_MCP_MAP, injects both
  • mcp_results.activities + mcp_results.weather pre-populated by orchestrator
  • needs_mcp dead-end removed — data always arrives ready
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

SERVICE_NAME      = "activity-agent"
AGENT_PORT        = int(os.getenv("AGENT_PORT",        "8004"))
AGENT_URL         = os.getenv("AGENT_URL",              f"http://activity-agent:{AGENT_PORT}")
OLLAMA_URL        = os.getenv("OLLAMA_URL",             "http://ollama:11434")
LLM_MODEL         = os.getenv("LLM_MODEL",              "llama3.2:1b")
OLLAMA_TIMEOUT    = int(os.getenv("OLLAMA_TIMEOUT",     "90"))
OLLAMA_KEEP_ALIVE = os.getenv("OLLAMA_KEEP_ALIVE",      "30m")
OLLAMA_NUM_PREDICT= int(os.getenv("OLLAMA_NUM_PREDICT", "256"))

app = Flask(__name__)
configure_logging(SERVICE_NAME)
meter = register_metrics(app, SERVICE_NAME)
init_tracing(app, SERVICE_NAME)
logger = logging.getLogger(SERVICE_NAME)
logger.setLevel(logging.INFO)

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

def _compact_rag(raw) -> List[str]:
    if not raw: return []
    if isinstance(raw, list):
        return [_trunc(d.get("content","") if isinstance(d,dict) else d, 220)
                for d in raw[:3]]
    return [_trunc(raw,220)]


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
                    {"role":"system","content":system_prompt},
                    {"role":"user",  "content":user_prompt},
                ],
                "stream":     False,
                "format":     "json",
                "keep_alive": OLLAMA_KEEP_ALIVE,
                "options":    {"num_predict":OLLAMA_NUM_PREDICT,"temperature":0.2},
            },
            headers=headers, timeout=OLLAMA_TIMEOUT,
        )
        resp.raise_for_status()
        content = resp.json()["message"]["content"]
        span.set_attribute("llm.output_tokens_est", len(content.split()))
        return content


def _run_activity_reasoning(task_id: str, task_payload: Dict[str, Any]) -> tuple[Dict,str]:
    tracer = get_tracer(SERVICE_NAME)
    with tracer.start_as_current_span("activity_agent_reason") as span:
        skill        = task_payload.get("skill",  "suggest_activities")
        intent       = task_payload.get("intent", "suggest_activities")
        user_context = task_payload.get("user_context", {}) or {}
        payload      = task_payload.get("payload", {}) or {}
        mcp_results  = task_payload.get("mcp_results", {}) or {}
        rag_context  = _compact_rag(task_payload.get("rag_context") or payload.get("rag_docs"))

        destination = user_context.get("destination") or payload.get("destination","?")
        season      = user_context.get("season") or payload.get("season")
        interests   = user_context.get("interests",[])

        # Activities from travel-mcp (via orchestrator)
        activities: List[Dict] = []
        raw_a = mcp_results.get("activities",{})
        if isinstance(raw_a, dict):
            activities = raw_a.get("activities", raw_a.get("results",[]))
        elif isinstance(raw_a, list):
            activities = raw_a

        # Weather from weather-mcp (via orchestrator) — same task, two MCP servers!
        weather = mcp_results.get("weather",{})
        if not isinstance(weather, dict):
            weather = {}

        _banner("ACTIVITY AGENT · TASK RECEIVED")
        _row("task_id",         task_id)
        _row("skill",           skill)
        _row("intent",          intent)
        _row("mcp_provided_by", "orchestrator (governance pattern)")
        _row("IMPORTANT",       "requires tools from 2 MCP servers:")
        _row("  travel-mcp",    "search_activities  → mcp_results.activities")
        _row("  weather-mcp",   "get_weather_forecast → mcp_results.weather")
        _sep()
        _row("activities from MCP", len(activities))
        _row("weather keys",        list(weather.keys()) if weather else "none")
        _row("destination",         destination)
        _row("season",              season)
        _row("interests",           interests)
        _sep()
        for i, a in enumerate(activities, 1):
            _row(f"  activity[{i}]", a, 250)
        if weather:
            _row("weather summary",
                 weather.get("summary", weather.get("condition", str(weather)[:200])))
        _banner_end()

        span.set_attribute("skill",            skill)
        span.set_attribute("activities.count", len(activities))
        span.set_attribute("has_weather",      bool(weather))
        span.set_attribute("destination",      destination)

        system_prompt = """You are activity-agent in an A2A multi-agent travel system.

GOVERNANCE MODEL: You do NOT call MCP tools. The orchestrator called:
  - travel-mcp/search_activities  → provided in activities_from_mcp
  - weather-mcp/get_weather_forecast → provided in weather_from_mcp

Your role: reason over BOTH data sources to select the best activities.
Use weather to filter out activities unsuitable on bad-weather days.
Match activities to the user's interests and season.

Return ONLY valid JSON:
{
  "intent": {"destination": "...", "season": "...", "interests": [...]},
  "reasoning": [
    "Step 1: examined N activities from travel-mcp",
    "Step 2: checked weather from weather-mcp for unsuitable days",
    "Step 3: filtered by user interests",
    "Step 4: ranked by fit score"
  ],
  "decision": {
    "selected_activities": [
      {
        "title": "...",
        "category": "...",
        "duration": "...",
        "price_eur": 0,
        "why_selected": "...",
        "fit_score": 0.85,
        "weather_suitable": true
      }
    ],
    "recommendation": "..."
  },
  "status": "completed",
  "a2a_request": null
}
"""
        user_prompt = json.dumps({
            "skill":              skill,
            "task":               intent,
            "user_context":       user_context,
            "activities_from_mcp": activities,
            "weather_from_mcp":   weather,
            "rag_context":        rag_context,
            "payload":            payload,
        }, ensure_ascii=False)

        _banner("ACTIVITY AGENT · LLM REASONING")
        _row("model",              LLM_MODEL)
        _row("activities provided",len(activities))
        _row("weather provided",   bool(weather))
        _row("mcp_servers_used",   "travel-mcp + weather-mcp (both via orchestrator)")
        _banner_end()

        try:
            raw_out  = ollama_chat(system_prompt, user_prompt)
            decision = json.loads(raw_out)
            decision["task_id"] = task_id
            decision["agent"]   = SERVICE_NAME
            status  = decision.get("status","completed")
            state   = "completed" if status != "error" else "failed"
            span.set_attribute("decision.status", status)
            selected = (decision.get("decision") or {}).get("selected_activities",[])
            span.set_attribute("selected.count", len(selected))

            _banner("ACTIVITY AGENT · LLM DECISION")
            _row("status", status)
            for r in (decision.get("reasoning") or []):
                _row("  reasoning", r, 220)
            for i, a in enumerate(selected, 1):
                _row(f"  [{i}] {a.get('title','?')}",
                     f"fit={a.get('fit_score','?')}  "
                     f"weather_ok={a.get('weather_suitable','?')}  "
                     f"→ {a.get('why_selected','?')}", 200)
            _row("recommendation",
                 (decision.get("decision") or {}).get("recommendation",""), 200)
            _banner_end()

            return decision, state

        except Exception as exc:
            logger.exception("activity-agent LLM error")
            fallback = {
                "task_id": task_id, "agent": SERVICE_NAME,
                "intent":  {"destination":destination,"season":season,"interests":interests},
                "reasoning": [f"LLM error: {exc}",
                              f"MCP: {len(activities)} activities, weather={'yes' if weather else 'no'}"],
                "decision": {
                    "selected_activities": [
                        {"title": a.get("name",a.get("title","?")),
                         "why_selected":"Fallback","fit_score":0.5,"weather_suitable":True}
                        for a in activities[:5]
                    ],
                    "recommendation": "Fallback — LLM error",
                },
                "status":"error","a2a_request":None,"error":str(exc),
            }
            return fallback, "failed"


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status":"ok","model":LLM_MODEL}), 200


@app.route("/.well-known/agent.json", methods=["GET"])
def agent_card():
    return jsonify(build_agent_card(
        name        = "activity-agent",
        description = (
            "LLM activity planner. Requires data from TWO MCP servers "
            "(travel-mcp for activities, weather-mcp for forecast). "
            "Orchestrator resolves both via SKILL_MCP_MAP. "
            "Agent never calls MCP directly."
        ),
        url     = f"{AGENT_URL}/a2a",
        version = "3.3.0",
        skills  = [{
            "id":                 "suggest_activities",
            "name":               "Suggest Activities",
            "description":        "Select activities from MCP data, filtered by weather forecast.",
            "requires_mcp_tools": ["search_activities", "get_weather_forecast"],
            "mcp_provided_by":    "orchestrator",
            "inputModes":         ["application/json"],
            "outputModes":        ["application/json"],
            "tags":               ["activities","travel"],
            "examples":           ["Suggest summer activities in Rome for museum lovers"],
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
    tracer = get_tracer(SERVICE_NAME)
    with tracer.start_as_current_span("activity_agent_tasks_send") as span:
        task_id      = params.get("id","unknown")
        session_id   = params.get("sessionId","unknown")
        task_payload = _extract_data_part(params.get("message",{})) or {}
        task_payload.setdefault("task_id", task_id)
        skill = task_payload.get("skill","suggest_activities")
        span.set_attribute("task.id",task_id)
        span.set_attribute("skill",  skill)

        decision, state = _run_activity_reasoning(task_id, task_payload)

        task = Task(
            id=task_id, session_id=session_id,
            status=TaskStatus(
                state=state,
                message=A2AMessage.agent_text(
                    f"[{SERVICE_NAME}] skill={skill}  status={decision.get('status')}"
                ).to_dict(),
            ),
            artifacts=[Artifact(
                name="activity_result",
                parts=[DataPart(decision).to_dict()],
            ).to_dict()],
            metadata={"agent":SERVICE_NAME,"model":LLM_MODEL,
                      "skill":skill,"mcp_provided_by":"orchestrator",
                      "mcp_servers_required":["travel-mcp","weather-mcp"]},
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
        "id":data.get("task_id","legacy-"+os.urandom(4).hex()),
        "sessionId":data.get("conversation_id","legacy"),
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
