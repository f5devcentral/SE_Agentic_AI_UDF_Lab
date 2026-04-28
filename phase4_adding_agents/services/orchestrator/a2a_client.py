"""
orchestrator/a2a_client.py
──────────────────────────
A2A client: builds and sends tasks/send JSON-RPC 2.0 requests to agents,
logs the full envelope both directions, returns the Task result dict.
"""

import logging
from typing import Any, Dict, List, Optional

import log_utils as L
import shared.tracing as tracing
from config import OLLAMA_TIMEOUT
from http_client import http_post_json
from shared.a2a_protocol import (
    A2AMessage, build_tasks_send, get_artifact_data,
)

logger = logging.getLogger("orchestrator")


def a2a_send_task(
    agent_name:  str,
    agent_url:   str,
    task_id:     str,
    session_id:  str,
    message:     A2AMessage,
    metadata:    Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Send tasks/send JSON-RPC 2.0 to an agent's /a2a endpoint.
    Logs the full outbound envelope and the full inbound response.
    Returns the Task dict from result (raises RuntimeError on JSON-RPC error).
    """
    headers = tracing.inject_trace_headers()
    headers["Content-Type"] = "application/json"
    rpc_request = build_tasks_send(task_id, session_id, message, metadata)

    # ── Log outbound ──────────────────────────────────────────────────────────
    L.banner(f"STEP 6 · A2A SEND → {agent_name.upper()}")
    L.row("jsonrpc",        "2.0")
    L.row("method",         "tasks/send")
    L.row("endpoint",       f"{agent_url}/a2a")
    L.row("task_id",        task_id)
    L.row("session_id",     session_id)
    if metadata:
        L.row("metadata.skill",          metadata.get("skill"))
        L.row("metadata.mcp_tools_used", metadata.get("mcp_tools_used"))
    L.sep()

    # Log the data part summary
    for part in message.to_dict().get("parts", []):
        if part.get("type") == "data":
            d  = part["data"]
            uc = d.get("user_context", {})
            L.row("  message.skill",        d.get("skill"))
            L.row("  message.intent",       d.get("intent"))
            L.row("  message.origin",       uc.get("origin"))
            L.row("  message.destination",  uc.get("destination"))
            L.row("  message.budget_eur",   uc.get("budget_eur"))
            L.row("  message.season",       uc.get("season"))
            L.row("  message.interests",    uc.get("interests"))
            mr       = d.get("mcp_results", {})
            mcp_keys = list(mr.keys()) if isinstance(mr, dict) else []
            L.row("  mcp_results keys",     mcp_keys)
            for k in mcp_keys:
                L.row(f"    mcp_results.{k}", mr[k], 300)

    # ── HTTP call ─────────────────────────────────────────────────────────────
    tracer = tracing.get_tracer("orchestrator")
    with tracer.start_as_current_span(f"a2a_{agent_name}_tasks_send"):
        resp        = http_post_json(
            f"{agent_name}-a2a",
            f"{agent_url}/a2a",
            rpc_request,
            timeout = OLLAMA_TIMEOUT,
            headers = headers,
        )
        rpc_response = resp.json()

    # ── Log inbound ───────────────────────────────────────────────────────────
    L.banner(f"STEP 6 · A2A RECV ← {agent_name.upper()}")

    if "error" in rpc_response:
        err = rpc_response["error"]
        logger.error("│  JSON-RPC ERROR  code=%s  msg=%s",
                     err.get("code"), err.get("message"))
        L.banner_end()
        raise RuntimeError(f"A2A error {err.get('code')}: {err.get('message')}")

    task      = rpc_response.get("result", {})
    state     = task.get("status", {}).get("state", "?")
    sp        = task.get("status", {}).get("message", {}).get("parts", [])
    status_txt = sp[0].get("text", "") if sp else ""
    artifacts  = task.get("artifacts", [])

    L.row("task_id",    task.get("id"))
    L.row("state",      state)
    L.row("status_msg", status_txt, 200)
    L.row("artifacts",  [a.get("name") for a in artifacts])
    L.row("metadata",   task.get("metadata", {}), 300)
    L.sep()

    for art in artifacts:
        art_name = art.get("name", "?")
        for part in art.get("parts", []):
            if part.get("type") == "data":
                d = part["data"]
                L.row(f"  [{art_name}] status",   d.get("status"))
                for r in (d.get("reasoning") or []):
                    L.row(f"  [{art_name}] reasoning", r, 220)
                decision = d.get("decision")
                if isinstance(decision, dict):
                    for dk, dv in decision.items():
                        L.row(f"  [{art_name}] decision.{dk}", dv, 350)
                elif decision is not None:
                    L.row(f"  [{art_name}] decision", decision, 350)
                if d.get("a2a_request"):
                    L.row(f"  [{art_name}] a2a_request", d["a2a_request"], 200)

    L.banner_end()
    return task
