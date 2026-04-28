"""
shared/a2a_protocol.py

A2A (Agent-to-Agent) protocol helpers — JSON-RPC 2.0 over HTTP.

Spec reference: https://google.github.io/A2A/specification/

Every A2A endpoint must:
  - Accept POST with Content-Type: application/json
  - Parse a JSON-RPC 2.0 request envelope
  - Return a JSON-RPC 2.0 response or error envelope
  - Expose /.well-known/agent.json (agent card)

Supported methods (server-side):
  tasks/send          — send a new task or continue one
  tasks/get           — poll task status by id
  tasks/cancel        — cancel a running task
  tasks/sendSubscribe — SSE streaming (optional, not implemented here)

The orchestrator uses the client helpers (a2a_send_task, a2a_get_task).
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger("a2a_protocol")

# ---------------------------------------------------------------------------
# JSON-RPC 2.0 helpers
# ---------------------------------------------------------------------------

JSONRPC = "2.0"


def jsonrpc_request(method: str, params: Dict[str, Any], req_id: str | None = None) -> Dict[str, Any]:
    return {
        "jsonrpc": JSONRPC,
        "id": req_id or str(uuid.uuid4()),
        "method": method,
        "params": params,
    }


def jsonrpc_success(result: Any, req_id: Any) -> Dict[str, Any]:
    return {"jsonrpc": JSONRPC, "id": req_id, "result": result}


def jsonrpc_error(code: int, message: str, req_id: Any, data: Any = None) -> Dict[str, Any]:
    err: Dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": JSONRPC, "id": req_id, "error": err}


# Standard JSON-RPC error codes
ERR_PARSE = -32700
ERR_INVALID_REQUEST = -32600
ERR_METHOD_NOT_FOUND = -32601
ERR_INVALID_PARAMS = -32602
ERR_INTERNAL = -32603

# A2A-specific error codes (in the -32000 range)
ERR_TASK_NOT_FOUND = -32001
ERR_TASK_CANCELLED = -32002
ERR_UNSUPPORTED_METHOD = -32003


# ---------------------------------------------------------------------------
# A2A data model (Task, Message, Artifact, Part)
# ---------------------------------------------------------------------------

@dataclass
class TextPart:
    text: str
    type: str = "text"

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "text": self.text}


@dataclass
class DataPart:
    data: Any
    type: str = "data"

    def to_dict(self) -> Dict[str, Any]:
        return {"type": self.type, "data": self.data}


@dataclass
class A2AMessage:
    role: str           # "user" | "agent"
    parts: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        return {"role": self.role, "parts": self.parts}

    @classmethod
    def user_text(cls, text: str) -> "A2AMessage":
        return cls(role="user", parts=[TextPart(text).to_dict()])

    @classmethod
    def user_data(cls, data: Any) -> "A2AMessage":
        return cls(role="user", parts=[DataPart(data).to_dict()])

    @classmethod
    def agent_text(cls, text: str) -> "A2AMessage":
        return cls(role="agent", parts=[TextPart(text).to_dict()])

    @classmethod
    def agent_data(cls, data: Any) -> "A2AMessage":
        return cls(role="agent", parts=[DataPart(data).to_dict()])


@dataclass
class Artifact:
    name: str
    parts: List[Dict[str, Any]]
    index: int = 0
    last_chunk: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "parts": self.parts,
            "index": self.index,
            "lastChunk": self.last_chunk,
        }


@dataclass
class TaskStatus:
    state: str                                  # submitted|working|input-required|completed|failed|canceled
    message: Optional[Dict[str, Any]] = None   # A2AMessage.to_dict()
    timestamp: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"state": self.state}
        if self.message:
            d["message"] = self.message
        if self.timestamp:
            d["timestamp"] = self.timestamp
        return d


@dataclass
class Task:
    id: str
    session_id: str
    status: TaskStatus
    history: List[Dict[str, Any]] = field(default_factory=list)
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "sessionId": self.session_id,
            "status": self.status.to_dict(),
            "history": self.history,
            "artifacts": self.artifacts,
            "metadata": self.metadata,
        }


# ---------------------------------------------------------------------------
# Agent Card (/.well-known/agent.json)
# ---------------------------------------------------------------------------

def build_agent_card(
    name: str,
    description: str,
    url: str,
    version: str,
    skills: List[Dict[str, Any]],
    capabilities: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Return a fully A2A-compliant agent card."""
    return {
        "name": name,
        "description": description,
        "url": url,
        "version": version,
        "capabilities": capabilities or {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": True,
        },
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": skills,
    }


# ---------------------------------------------------------------------------
# Flask helpers — parse incoming JSON-RPC, dispatch, return response
# ---------------------------------------------------------------------------

def parse_jsonrpc(raw: Dict[str, Any]) -> tuple[str | None, str, Dict[str, Any]]:
    """
    Returns (req_id, method, params) or raises ValueError on malformed input.
    """
    if raw.get("jsonrpc") != JSONRPC:
        raise ValueError("Not a JSON-RPC 2.0 request")
    method = raw.get("method")
    if not method:
        raise ValueError("Missing method")
    params = raw.get("params", {})
    req_id = raw.get("id")
    return req_id, method, params


# ---------------------------------------------------------------------------
# Client — send a task to a remote A2A agent
# ---------------------------------------------------------------------------

def build_tasks_send(
    task_id: str,
    session_id: str,
    message: A2AMessage,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build a tasks/send JSON-RPC request."""
    params: Dict[str, Any] = {
        "id": task_id,
        "sessionId": session_id,
        "message": message.to_dict(),
    }
    if metadata:
        params["metadata"] = metadata
    return jsonrpc_request("tasks/send", params, req_id=task_id)


def build_tasks_get(task_id: str) -> Dict[str, Any]:
    return jsonrpc_request("tasks/get", {"id": task_id})


def build_tasks_cancel(task_id: str) -> Dict[str, Any]:
    return jsonrpc_request("tasks/cancel", {"id": task_id})


def extract_task_result(response: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract the Task dict from a tasks/send or tasks/get response.
    Raises KeyError if the response contains a JSON-RPC error.
    """
    if "error" in response:
        err = response["error"]
        raise RuntimeError(f"A2A error {err.get('code')}: {err.get('message')}")
    return response.get("result", {})


def get_artifact_data(task: Dict[str, Any], artifact_name: str) -> Any:
    """Pull the first data part from a named artifact."""
    for art in task.get("artifacts", []):
        if art.get("name") == artifact_name:
            for part in art.get("parts", []):
                if part.get("type") == "data":
                    return part.get("data")
    return None
