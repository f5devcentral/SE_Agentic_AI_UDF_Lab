"""
orchestrator/app.py  —  Flask entry point  v3.3.1
──────────────────────────────────────────────────
This file is intentionally thin.  All logic lives in dedicated modules:

  config.py            env vars + SKILL_MCP_MAP registry
  log_utils.py         banner/row/box logging helpers
  http_client.py       http_post_json / http_get
  context_extractor.py NLP intent extraction from prompt
  rag_client.py        RAG search + context builder
  agent_discovery.py   GET /.well-known/agent.json per agent
  mcp_gateway.py       MCP initialize + tools/call + governance resolve
  a2a_client.py        tasks/send + tasks/recv logging
  pipeline.py          full planning pipeline (calls all of the above)
  app.py               Flask routes only (this file)
"""

import logging
import uuid

from flask import Flask, jsonify, request

from shared.a2a_protocol import (
    A2AMessage, Artifact, DataPart,
    ERR_INVALID_PARAMS, ERR_METHOD_NOT_FOUND,
    Task, TaskStatus,
    build_agent_card,
    jsonrpc_error, jsonrpc_success, parse_jsonrpc,
)
from shared.logging import configure_logging
from shared.metrics import register_metrics
import shared.tracing as tracing
from shared.tracing import init_tracing

from config import (
    SERVICE_NAME, SERVICE_VERSION, LLM_MODEL,
    ORCHESTRATOR_URL, TRAVEL_MCP_URL, WEATHER_MCP_URL, SKILL_MCP_MAP,
)
import log_utils as L
from pipeline import run_planning_pipeline

# ── App bootstrap ─────────────────────────────────────────────────────────────

app = Flask(__name__)
configure_logging(SERVICE_NAME)
meter = register_metrics(app, SERVICE_NAME)
init_tracing(app, SERVICE_NAME)

logger = logging.getLogger(SERVICE_NAME)
logger.setLevel(logging.INFO)


# ── Routes ────────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status":  "ok",
        "model":   LLM_MODEL,
        "version": SERVICE_VERSION,
    }), 200


@app.route("/.well-known/agent.json", methods=["GET"])
def agent_card():
    card = build_agent_card(
        name        = SERVICE_NAME,
        description = (
            "Central A2A orchestrator with MCP governance. "
            "Discovers agents, resolves their skill→MCP requirements through the "
            "SKILL_MCP_MAP registry (keyed by tool name), calls all MCP tools "
            "on behalf of agents, then fans out enriched tasks via A2A tasks/send."
        ),
        url     = f"{ORCHESTRATOR_URL}/a2a",
        version = SERVICE_VERSION,
        skills  = [{
            "id":              "plan_trip",
            "name":            "Plan Trip",
            "description":     "End-to-end travel planning with central MCP governance.",
            "mcp_tools_owned": list(SKILL_MCP_MAP.keys()),
            "mcp_servers":     [TRAVEL_MCP_URL, WEATHER_MCP_URL],
            "inputModes":      ["application/json"],
            "outputModes":     ["application/json"],
            "tags":            ["travel", "orchestration", "governance"],
        }],
    )
    return jsonify(card), 200


@app.route("/a2a", methods=["POST"])
def a2a_endpoint():
    """JSON-RPC 2.0 A2A endpoint — primary entry point for the frontend."""
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


def _handle_tasks_send(req_id, params):
    tracer = tracing.get_tracer(SERVICE_NAME)
    with tracer.start_as_current_span("orchestrator_tasks_send"):

        task_id    = params.get("id")       or str(uuid.uuid4())
        session_id = params.get("sessionId") or str(uuid.uuid4())
        message    = params.get("message", {})
        prompt     = _extract_text(message)

        if not prompt:
            return jsonify(jsonrpc_error(
                ERR_INVALID_PARAMS,
                "message must contain a text part with the user prompt",
                req_id,
            )), 400

        L.box(f"ORCHESTRATOR v{SERVICE_VERSION}  —  NEW TASK")
        logger.info("  task_id    : %s", task_id)
        logger.info("  session_id : %s", session_id)
        logger.info("  prompt     : %s", prompt)
        logger.info("  tokens     : %d", L.count_tokens(prompt))

        try:
            itinerary = run_planning_pipeline(prompt, task_id, session_id)
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
            name  = "itinerary",
            parts = [DataPart(itinerary).to_dict()],
        )
        task = Task(
            id=task_id, session_id=session_id,
            status=TaskStatus(
                state="completed",
                message=A2AMessage.agent_text(
                    f"Trip planned for {itinerary.get('destination', 'destination')}."
                ).to_dict(),
            ),
            artifacts = [artifact.to_dict()],
            metadata  = {"model": LLM_MODEL, "version": SERVICE_VERSION},
        )
        return jsonify(jsonrpc_success(task.to_dict(), req_id)), 200


def _extract_text(message: dict) -> str:
    for part in message.get("parts", []):
        if part.get("type") == "text":
            return part.get("text", "")
        if part.get("type") == "data":
            data = part.get("data", {})
            if isinstance(data, dict):
                return data.get("prompt", "")
    return ""


@app.route("/plan", methods=["POST"])
def plan_trip():
    """Legacy REST endpoint — wraps the same pipeline."""
    tracer = tracing.get_tracer(SERVICE_NAME)
    with tracer.start_as_current_span("plan_trip_rest"):
        body   = request.get_json(force=True) or {}
        prompt = body.get("prompt", "")
        if not prompt:
            return jsonify({"error": "prompt required"}), 400
        cid = str(uuid.uuid4())
        try:
            itinerary = run_planning_pipeline(prompt, cid, cid)
        except Exception as exc:
            logger.exception("Pipeline failed")
            return jsonify({"error": str(exc)}), 500
        return jsonify(itinerary)


if __name__ == "__main__":
    logger.info("ORCHESTRATOR v%s STARTED  model=%s", SERVICE_VERSION, LLM_MODEL)
    app.run(host="0.0.0.0", port=9000, debug=False)
