"""
orchestrator-direct/log_utils.py
──────────────────────────────────
Identical to orchestrator/log_utils.py with two additions:

  log_token_accounting()   — logs prompt / RAG / MCP / total token counts
                             so they appear in the logviz output

narrate_skill() is removed — the direct orchestrator has no skill dispatch.
"""

import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("orchestrator-direct")

W = 72   # banner width


# ── Major box (phase boundary) ────────────────────────────────────────────────

def box(title: str):
    inner = f"  {title}  "
    pad   = max(0, W - len(inner) - 2)
    left  = pad // 2
    right = pad - left
    logger.info("╔%s╗", "═" * W)
    logger.info("║%s%s%s║", " " * left, inner, " " * right)
    logger.info("╚%s╝", "═" * W)


# ── Step banner ───────────────────────────────────────────────────────────────

def banner(title: str):
    pad = max(0, W - len(title) - 4)
    logger.info("┌─ %s %s", title, "─" * pad)


def banner_end():
    logger.info("└%s", "─" * (W - 1))


def sep():
    logger.info("│  %s", "·" * (W - 4))


# ── Row inside a banner ───────────────────────────────────────────────────────

def row(label: str, value: Any = None, limit: int = 1200):
    if value is None:
        logger.info("│  %s", label)
    else:
        logger.info("│  %-32s %s", label + ":", trunc(value, limit))


# ── Token accounting ──────────────────────────────────────────────────────────

def count_tokens(text: str) -> int:
    """
    Rough whitespace-split token estimate.
    Real tokenizers differ by ~10-15% but this is consistent and fast.
    """
    return len(text.split()) if text else 0


def log_token_accounting(
    label:         str,
    prompt_tokens: int,
    rag_tokens:    int,
    mcp_tokens:    int,
    total_tokens:  int,
    max_tokens:    Optional[int] = None,
) -> None:
    """
    Emit a token-accounting block in the log.  Call this:
      - after Step 2 (RAG) with rag_tokens filled in
      - before the LLM call (Step 7) with all fields filled in

    This is the data that was missing from logviz — it now appears as a
    clearly labelled banner so it's visible when comparing agentic vs direct.
    """
    banner(f"TOKEN ACCOUNTING  ·  {label}")
    row("prompt_tokens",   f"{prompt_tokens:,}")
    row("rag_tokens",      f"{rag_tokens:,}  ({RAG_DOC_LIMIT_hint()} docs)")
    row("mcp_data_tokens", f"{mcp_tokens:,}  (flights + hotels + activities + weather)")
    row("total_input_tokens", f"{total_tokens:,}")
    if max_tokens:
        row("llm_max_output_tokens", f"{max_tokens:,}")
        pct = round(total_tokens / max(1, total_tokens + max_tokens) * 100)
        row("input_pct_of_context", f"{pct}%  (input / input+output)")
    banner_end()


def RAG_DOC_LIMIT_hint() -> str:
    try:
        from config import RAG_DOC_LIMIT
        return str(RAG_DOC_LIMIT)
    except ImportError:
        return "?"


# ── Truncation ────────────────────────────────────────────────────────────────

def trunc(value, limit: int = 1000) -> str:
    if value is None:
        return "None"
    if isinstance(value, (dict, list)):
        try:
            value = json.dumps(value, ensure_ascii=False)
        except Exception:
            value = str(value)
    else:
        value = str(value)
    return value if len(value) <= limit else value[:limit] + f"… [{len(value)-limit} chars]"


# ── HTTP request/response loggers ─────────────────────────────────────────────

def log_http_out(name: str, method: str, url: str, payload=None, headers=None):
    logger.info("[OUT→] %-18s %s %s", name.upper(), method, url)
    if headers:
        safe = {k: v for k, v in headers.items()
                if k.lower() not in {"authorization", "cookie", "x-api-key"}}
        if safe:
            logger.debug("[OUT→] %-18s HEADERS: %s", name.upper(), safe)
    if payload is not None:
        try:
            size = len(json.dumps(payload, ensure_ascii=False))
        except Exception:
            size = 0
        logger.info("[OUT→] %-18s PAYLOAD (%d bytes): %s",
                    name.upper(), size, trunc(payload, 900))


def log_http_in(name: str, resp, elapsed: float):
    try:
        body = resp.json()
        logger.info("[←IN ] %-18s HTTP %s  %.3fs  %d bytes",
                    name.upper(), resp.status_code, elapsed, len(resp.content))
        logger.info("[←IN ] %-18s RESPONSE: %s", name.upper(), trunc(body, 1200))
        return body
    except Exception:
        body = resp.text
        logger.info("[←IN ] %-18s HTTP %s  %.3fs  TEXT: %s",
                    name.upper(), resp.status_code, elapsed, trunc(body, 800))
        return body
