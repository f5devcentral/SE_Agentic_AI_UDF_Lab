"""
orchestrator/log_utils.py
─────────────────────────
Structured logging helpers shared across all orchestrator modules.

Banner format makes log tailing easy during demos:
  ╔════════════════════╗  ← box()        major phase transitions
  ┌─ STEP TITLE ───────  ← banner()      individual steps
  │  label:   value       ← row()
  │  ·········            ← sep()
  └───────────────────   ← banner_end()

  ┌─ SKILL RECIPE ·····  ← narrate_skill()  pre-dispatch skill explanation
"""

import json
import logging
from typing import Any

logger = logging.getLogger("orchestrator")

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


# ── Skill recipe narration ────────────────────────────────────────────────────

def narrate_skill(skill_id: str, agent_name: str) -> None:
    """
    Log a human-readable recipe card for a skill immediately before the
    orchestrator dispatches it to the downstream agent.

    This makes it clear to demo observers:
      - WHAT the skill does
      - WHAT data it consumes
      - WHAT artifact it will produce
      - WHY the orchestrator is invoking it at this point in the pipeline

    The SKILL_RECIPES dict is defined in config.py and imported here lazily
    to avoid a circular import (config → log_utils would be circular).
    """
    # lazy import to avoid circular dependency
    try:
        from config import SKILL_RECIPES
        recipe = SKILL_RECIPES.get(skill_id)
    except ImportError:
        recipe = None

    if not recipe:
        # skill not in catalogue — emit a minimal notice and continue
        banner(f"SKILL RECIPE · {skill_id.upper()}  (no catalogue entry)")
        row("agent",  agent_name)
        row("skill",  skill_id)
        row("note",   "No recipe registered in SKILL_RECIPES — dispatching as-is")
        banner_end()
        return

    verb = recipe.get("verb", "DISPATCH")
    banner(f"SKILL RECIPE · {verb} · {skill_id.upper()}")
    row("agent",           agent_name)
    row("skill_id",        skill_id)
    row("what it does",    recipe.get("summary", "—"))
    row("inputs consumed", ", ".join(recipe.get("inputs", [])))
    row("artifact output", recipe.get("outputs", "—"))
    row("why now",         recipe.get("why", "—"))
    banner_end()


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


# ── Token counter + accounting ────────────────────────────────────────────────

def count_tokens(text: str) -> int:
    """Rough word-split token estimate — matches agent-side counting."""
    return len(text.split()) if text else 0


def log_token_accounting(
    label:         str,
    prompt_tokens: int,
    rag_tokens:    int,
    mcp_tokens:    int    = 0,
    total_tokens:  int    = 0,
    max_tokens:    int    = None,
) -> None:
    """
    Emit a token-accounting banner.

    Call sites in pipeline.py:
      • after Step 2 (RAG)  — prompt + rag tokens visible early
      • before each A2A dispatch — prompt + rag + per-agent MCP slice

    Was missing from logviz output — now appears as a clearly labelled
    banner so the demo audience can see token consumption at each stage.
    """
    banner(f"TOKEN ACCOUNTING  ·  {label}")
    row("prompt_tokens",      f"{prompt_tokens:,}")
    row("rag_context_tokens", f"{rag_tokens:,}")
    if mcp_tokens:
        row("mcp_data_tokens",  f"{mcp_tokens:,}")
    row("total_input_tokens", f"{total_tokens:,}")
    if max_tokens:
        row("agent_max_output",  f"{max_tokens:,}  tokens")
    banner_end()


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
