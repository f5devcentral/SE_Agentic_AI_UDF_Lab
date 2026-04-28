"""
services/weather-agent/app.py  —  A2A Weather Agent  v3.3.0
════════════════════════════════════════════════════════════

Governance pattern:
  • NO MCP URLs — agent never calls MCP directly
  • Declares requires_mcp_tools: ["get_weather_forecast"]
  • mcp_results.weather pre-populated by orchestrator
  • Adds packing_tips + activity_impact to decision
"""

import json
import logging
import os
from typing import Any, Dict

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

SERVICE_NAME    = "weather-agent"
AGENT_PORT      = int(os.getenv("AGENT_PORT",      "8005"))
AGENT_URL       = os.getenv("AGENT_URL",            f"http://weather-agent:{AGENT_PORT}")
OLLAMA_URL      = os.getenv("OLLAMA_URL",           "http://ollama:11434")
LLM_MODEL       = os.getenv("LLM_MODEL",            "llama3.2:1b")
OLLAMA_TIMEOUT  = int(os.getenv("OLLAMA_TIMEOUT",   "120"))

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
                    {"role":"system","content":system_prompt},
                    {"role":"user",  "content":user_prompt},
                ],
                "stream": False, "format": "json",
            },
            headers=headers, timeout=OLLAMA_TIMEOUT,
        )
        resp.raise_for_status()
        content = resp.json()["message"]["content"]
        span.set_attribute("llm.output_tokens_est", len(content.split()))
        return content


def _run_weather_reasoning(task_id: str, task_payload: Dict[str, Any]) -> tuple[Dict,str]:
    with tracer.start_as_current_span("weather_agent_reason") as span:
        skill        = task_payload.get("skill",  "get_forecast")
        intent       = task_payload.get("intent", "get_forecast")
        user_context = task_payload.get("user_context", {})
        payload      = task_payload.get("payload", {})
        mcp_results  = task_payload.get("mcp_results", {})

        weather = mcp_results.get("weather", {})
        if not isinstance(weather, dict):
            weather = {}

        destination = user_context.get("destination") or payload.get("destination","?")
        preferences = user_context.get("preferences")

        _banner("WEATHER AGENT · TASK RECEIVED")
        _row("task_id",         task_id)
        _row("skill",           skill)
        _row("intent",          intent)
        _row("mcp_provided_by", "orchestrator (governance pattern)")
        _row("destination",     destination)
        _row("weather keys",    list(weather.keys()) if weather else "none")
        _sep()
        if weather:
            _row("forecast data", weather, 600)
        else:
            _row("⚠ no weather data", "MCP result was empty or errored")
        _banner_end()

        span.set_attribute("skill",         skill)
        span.set_attribute("has_weather",   bool(weather))
        span.set_attribute("forecast.keys", len(weather))

        system_prompt = """You are weather-agent in an A2A multi-agent travel system.

GOVERNANCE MODEL: You do NOT call MCP tools. The orchestrator called
weather-mcp/get_weather_forecast on your behalf and injected the results below.
Your role is to REASON over the provided forecast and turn it into travel guidance.

Return ONLY valid JSON:
{
  "summary": "overall weather summary for the trip",
  "good_days": ["YYYY-MM-DD", ...],
  "bad_days":  ["YYYY-MM-DD", ...],
  "packing_tips": ["bring sunscreen", "pack a light rain jacket"],
  "activity_impact": "brief note on how weather affects planned activities",
  "status": "completed",
  "a2a_request": null
}
"""
        user_prompt = json.dumps({
            "skill":             skill,
            "task":              intent,
            "destination":       destination,
            "preferences":       preferences,
            "forecast_from_mcp": weather,
        })

        _banner("WEATHER AGENT · LLM REASONING")
        _row("model",            LLM_MODEL)
        _row("forecast provided",bool(weather))
        _banner_end()

        try:
            llm_raw  = ollama_chat(system_prompt, user_prompt)
            decision = json.loads(llm_raw)
            status   = decision.get("status","completed")
            state    = "completed" if status != "error" else "failed"
            span.set_attribute("status",          status)
            span.set_attribute("good_days.count", len(decision.get("good_days",[])))
            span.set_attribute("bad_days.count",  len(decision.get("bad_days",[])))

            _banner("WEATHER AGENT · LLM DECISION")
            _row("status",          status)
            _row("summary",         decision.get("summary",""), 200)
            _row("good_days",       decision.get("good_days",[]))
            _row("bad_days",        decision.get("bad_days",[]))
            for tip in (decision.get("packing_tips") or []):
                _row("  packing_tip",   tip, 150)
            _row("activity_impact", decision.get("activity_impact",""), 200)
            _banner_end()

        except json.JSONDecodeError:
            logger.warning("weather-agent JSON decode failed, fallback")
            span.set_status("error","JSON decode failed")
            decision = {
                "summary":         "Weather analysis unavailable — LLM error.",
                "good_days":       [], "bad_days": [],
                "packing_tips":    [], "activity_impact": "",
                "status":          "error",
            }
            state = "failed"

        return decision, state


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status":"ok"}), 200


@app.route("/.well-known/agent.json", methods=["GET"])
def agent_card():
    return jsonify(build_agent_card(
        name        = "weather-agent",
        description = ("LLM weather analyst. Declares required MCP tool so the "
                       "orchestrator fetches the forecast on its behalf. "
                       "Turns real forecast data into traveller-friendly guidance."),
        url     = f"{AGENT_URL}/a2a",
        version = "3.3.0",
        skills  = [{
            "id":                 "get_forecast",
            "name":               "Get Forecast",
            "description":        "Analyse real weather forecast and provide trip guidance.",
            "requires_mcp_tools": ["get_weather_forecast"],
            "mcp_provided_by":    "orchestrator",
            "inputModes":         ["application/json"],
            "outputModes":        ["application/json"],
            "tags":               ["weather","travel"],
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
    with tracer.start_as_current_span("weather_agent_tasks_send") as span:
        task_id      = params.get("id","unknown")
        session_id   = params.get("sessionId","unknown")
        task_payload = _extract_data_part(params.get("message",{})) or {}
        skill        = task_payload.get("skill","get_forecast")
        span.set_attribute("task.id",task_id)
        span.set_attribute("skill",  skill)

        decision, state = _run_weather_reasoning(task_id, task_payload)

        task = Task(
            id=task_id, session_id=session_id,
            status=TaskStatus(
                state=state,
                message=A2AMessage.agent_text(
                    f"[{SERVICE_NAME}] skill={skill}  status={decision.get('status')}"
                ).to_dict(),
            ),
            artifacts=[Artifact(
                name="weather_result",
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
