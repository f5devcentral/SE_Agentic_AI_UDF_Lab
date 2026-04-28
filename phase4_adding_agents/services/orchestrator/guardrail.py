"""
guardrail.py  —  F5 AI Security (CalypsoAI) Inference Defend guardrail
========================================================================
Rewritten from working integration code.

Real API shape (from tested code):
  cai.scans.scan(text, project="<project-uuid>")
  result.outcome  →  "cleared" | "flagged"
  scannerResults[].outcome  →  "passed" | "failed"   (failed = scanner triggered)

Four scan points in the pipeline:
  1. scan_user_prompt(prompt)    — raw user prompt, before any processing
  2. scan_rag_context(rag_text)  — RAG retrieved docs, before LLM injection
  3. scan_mcp_data(mcp_cache)    — MCP tool results, before LLM injection
  4. scan_llm_response(text)     — LLM response, before returning to user

For each failed scanner, the scanner name is fetched from:
  GET /backend/v1/scanners/{scannerId}
and logged in the guardrail block so the operator knows which policy triggered.

Environment variables
─────────────────────
F5GUARDRAILS_ENABLED   true | false           (default: false)
F5GUARDRAILS_URL       https://www.us1.calypsoai.app
F5GUARDRAILS_TOKEN     <API token>
F5GUARDRAILS_PROJECT   <project UUID>
F5GUARDRAILS_TIMEOUT   seconds                (default: 10)

Return shape from every scan_*() call:
{
    "allowed":         bool,      # True = cleared, False = flagged (block)
    "outcome":         str,       # "cleared" | "flagged" | "skipped" | "error"
    "scan_id":         str|None,  # CalypsoAI scan UUID for audit trail
    "failed_scanners": [          # populated when outcome == "flagged"
        {"id": str, "name": str, "direction": str}
    ],
    "blocked_reason":  str|None,
}
"""

import json
import logging
import os
from functools import lru_cache
from typing import Any, Dict, List, Optional

import requests

import log_utils as L

logger = logging.getLogger("orchestrator")

# ── Config ────────────────────────────────────────────────────────────────────
F5GUARDRAILS_ENABLED = os.getenv("F5GUARDRAILS_ENABLED", "false").lower() == "true"
F5GUARDRAILS_URL     = os.getenv("F5GUARDRAILS_URL",     "https://www.us1.calypsoai.app")
F5GUARDRAILS_TOKEN   = os.getenv("F5GUARDRAILS_TOKEN",   "")
F5GUARDRAILS_PROJECT = os.getenv("F5GUARDRAILS_PROJECT", "")
F5GUARDRAILS_TIMEOUT = int(os.getenv("F5GUARDRAILS_TIMEOUT", "10"))

_cai_client = None


def _get_client():
    global _cai_client
    if _cai_client is None:
        from calypsoai import CalypsoAI
        _cai_client = CalypsoAI(url=F5GUARDRAILS_URL, token=F5GUARDRAILS_TOKEN)
        logger.info("[GUARDRAIL] CalypsoAI client initialised  url=%s  project=%s",
                    F5GUARDRAILS_URL, F5GUARDRAILS_PROJECT)
    return _cai_client


@lru_cache(maxsize=128)
def _fetch_scanner_name(scanner_id: str) -> str:
    """GET /backend/v1/scanners/{id} → scanner.name (cached)."""
    try:
        url  = f"{F5GUARDRAILS_URL}/backend/v1/scanners/{scanner_id}"
        hdrs = {"Authorization": f"Bearer {F5GUARDRAILS_TOKEN}"}
        resp = requests.get(url, headers=hdrs, timeout=F5GUARDRAILS_TIMEOUT)
        resp.raise_for_status()
        return resp.json().get("scanner", {}).get("name", scanner_id)
    except Exception as exc:
        logger.debug("[GUARDRAIL] scanner name lookup failed %s: %s", scanner_id, exc)
        return scanner_id


def _skipped() -> Dict[str, Any]:
    return {"allowed": True, "outcome": "skipped",
            "scan_id": None, "failed_scanners": [], "blocked_reason": None}


def _error_result(exc: Exception) -> Dict[str, Any]:
    return {"allowed": True, "outcome": "error",
            "scan_id": None, "failed_scanners": [],
            "blocked_reason": f"Guardrail error (fail-open): {exc}"}


def _parse_result(data: Dict[str, Any]) -> Dict[str, Any]:
    outcome  = data.get("result", {}).get("outcome", "unknown")
    scanners = data.get("result", {}).get("scannerResults", [])
    scan_id  = data.get("id")

    failed: List[Dict[str, str]] = []
    for sr in scanners:
        if sr.get("outcome") == "failed":
            sid  = sr.get("scannerId", "?")
            name = _fetch_scanner_name(sid)
            failed.append({
                "id":        sid,
                "name":      name,
                "direction": sr.get("scanDirection", "?"),
            })

    blocked_reason = None
    if outcome == "flagged" and failed:
        blocked_reason = "Flagged by: " + ", ".join(f["name"] for f in failed)

    return {
        "allowed":         outcome == "cleared",
        "outcome":         outcome,
        "scan_id":         scan_id,
        "failed_scanners": failed,
        "blocked_reason":  blocked_reason,
    }


def _log_result(label: str, result: Dict[str, Any], char_count: int, token_count: int):
    outcome = result["outcome"]
    icon    = "✓" if outcome == "cleared" else ("✗" if outcome == "flagged" else "?")

    L.banner(f"GUARDRAIL · F5 AI SECURITY · {label}")
    L.row("project",   F5GUARDRAILS_PROJECT)
    L.row("scan_id",   result["scan_id"])
    L.row("input",     f"{char_count} chars  /  ~{token_count} tokens")
    L.row("outcome",   f"{icon}  {outcome.upper()}")
    if result["failed_scanners"]:
        L.row("failed_scanners", "")
        for fs in result["failed_scanners"]:
            L.row(f"  ✗  {fs['name']}",
                  f"scannerId={fs['id']}  direction={fs['direction']}")
    else:
        L.row("scanners", "all passed")
    if result["blocked_reason"]:
        L.row("blocked_reason", result["blocked_reason"])
    L.banner_end()


def _run_scan(text: str, label: str) -> Dict[str, Any]:
    if not F5GUARDRAILS_ENABLED:
        return _skipped()
    if not F5GUARDRAILS_TOKEN or not F5GUARDRAILS_PROJECT:
        logger.warning("[GUARDRAIL] F5GUARDRAILS_TOKEN or F5GUARDRAILS_PROJECT not set — skipping")
        return _skipped()
    try:
        cai    = _get_client()
        result = cai.scans.scan(text, project=F5GUARDRAILS_PROJECT)
        parsed = _parse_result(result.model_dump())
        _log_result(label, parsed, len(text), L.count_tokens(text))
        return parsed
    except Exception as exc:
        logger.error("[GUARDRAIL] scan failed [%s]: %s", label, exc)
        L.banner(f"GUARDRAIL · F5 AI SECURITY · {label}  [ERROR]")
        L.row("error",  str(exc))
        L.row("action", "fail-open — pipeline continues")
        L.banner_end()
        return _error_result(exc)


# ═════════════════════════════════════════════════════════════════════════════
# Public scan functions  —  one per pipeline stage
# ═════════════════════════════════════════════════════════════════════════════

def scan_user_prompt(prompt: str) -> Dict[str, Any]:
    """SCAN POINT 1 — raw user input before any processing."""
    return _run_scan(prompt, "INPUT · user prompt")


def scan_rag_context(rag_context: str) -> Dict[str, Any]:
    """SCAN POINT 2 — RAG retrieved docs (guards against RAG poisoning)."""
    if not rag_context or not rag_context.strip():
        return _skipped()
    return _run_scan(rag_context, "RAG · retrieved context")


def scan_mcp_data(mcp_cache: Dict[str, Any]) -> Dict[str, Any]:
    """SCAN POINT 3 — MCP tool results before LLM injection."""
    if not mcp_cache:
        return _skipped()
    text = json.dumps(mcp_cache, ensure_ascii=False, separators=(",", ":"))
    return _run_scan(text, "MCP · tool results")


def scan_llm_response(response_text: str) -> Dict[str, Any]:
    """SCAN POINT 4 — LLM output before returning to user."""
    if not response_text or not response_text.strip():
        return _skipped()
    return _run_scan(response_text, "OUTPUT · LLM response")


def guardrail_blocked_message(blocked_reason: str, scan_point: str) -> str:
    return (
        f"Your request was blocked by the F5 AI Security guardrail "
        f"at the {scan_point} stage. "
        f"Reason: {blocked_reason}. "
        f"Please rephrase your request or contact your administrator."
    )
