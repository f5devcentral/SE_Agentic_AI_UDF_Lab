"""
orchestrator-direct/app.py  —  Flask entry point  v1.0.0
──────────────────────────────────────────────────────────
Identical structure to orchestrator/app.py.
The only meaningful differences:
  • imports pipeline from orchestrator-direct/pipeline.py (no agents)
  • SERVICE_NAME = "orchestrator-direct"
  • listens on port 9001 (9000 is taken by the agentic orchestrator)
  • agent card describes the direct mode

All logic lives in dedicated modules — this file is routes only.
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
        "mode":    "direct",
        "model":   LLM_MODEL,
        "version": SERVICE_VERSION,
    }), 200


@app.route("/.well-known/agent.json", methods=["GET"])
def agent_card():
    card = build_agent_card(
        name        = SERVICE_NAME,
        description = (
            "Non-agentic direct orchestrator. "
            "Runs the same MCP governance (Steps 1-6) as the agentic orchestrator "
            "but replaces the A2A fan-out with a single central LLM call. "
            "Lower token consumption, same data inputs, slightly lower accuracy."
        ),
        url     = f"{ORCHESTRATOR_URL}/a2a",
        version = SERVICE_VERSION,
        skills  = [{
            "id":          "plan_trip_direct",
            "name":        "Plan Trip (Direct)",
            "description": "Single-LLM travel planning with full MCP governance.",
            "mcp_tools_owned": list(SKILL_MCP_MAP.keys()),
            "mcp_servers":     [TRAVEL_MCP_URL, WEATHER_MCP_URL],
            "inputModes":      ["application/json"],
            "outputModes":     ["application/json"],
            "tags":            ["travel", "direct", "single-llm"],
        }],
    )
    return jsonify(card), 200


@app.route("/a2a", methods=["POST"])
def a2a_endpoint():
    """JSON-RPC 2.0 A2A endpoint — same interface as agentic orchestrator."""
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
    with tracer.start_as_current_span("orchestrator_direct_tasks_send"):

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

        L.box(f"ORCHESTRATOR-DIRECT v{SERVICE_VERSION}  —  NEW TASK")
        logger.info("  task_id    : %s", task_id)
        logger.info("  session_id : %s", session_id)
        logger.info("  prompt     : %s", prompt)
        logger.info("  tokens     : %d", L.count_tokens(prompt))

        try:
            itinerary = run_planning_pipeline(prompt, task_id, session_id)
        except Exception as exc:
            logger.exception("Direct pipeline failed")
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
                    f"Trip planned for {itinerary.get('destination', 'destination')} "
                    f"(direct mode, 1 LLM call)."
                ).to_dict(),
            ),
            artifacts = [artifact.to_dict()],
            metadata  = {
                "model":   LLM_MODEL,
                "version": SERVICE_VERSION,
                "mode":    "direct",
                "token_accounting": itinerary.get("token_accounting", {}),
            },
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
    """Legacy REST endpoint — same as agentic orchestrator."""
    tracer = tracing.get_tracer(SERVICE_NAME)
    with tracer.start_as_current_span("plan_trip_direct_rest"):
        body   = request.get_json(force=True) or {}
        prompt = body.get("prompt", "")
        if not prompt:
            return jsonify({"error": "prompt required"}), 400
        cid = str(uuid.uuid4())
        try:
            itinerary = run_planning_pipeline(prompt, cid, cid)
        except Exception as exc:
            logger.exception("Direct pipeline failed")
            return jsonify({"error": str(exc)}), 500
        return jsonify(itinerary)


if __name__ == "__main__":
    logger.info("ORCHESTRATOR-DIRECT v%s STARTED  model=%s", SERVICE_VERSION, LLM_MODEL)
    app.run(host="0.0.0.0", port=9001, debug=False)
