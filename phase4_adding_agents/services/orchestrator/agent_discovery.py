"""
orchestrator/agent_discovery.py
────────────────────────────────
Discovers downstream A2A agents by fetching their /.well-known/agent.json cards.
Logs the full skill + requires_mcp_tools structure so it's visible in demos.
"""

import logging
from typing import Any, Dict

import log_utils as L
import shared.tracing as tracing
from config import AGENT_URLS
from http_client import http_get

logger = logging.getLogger("orchestrator")


def discover_agents() -> Dict[str, Any]:
    """
    GET /.well-known/agent.json from every configured agent.
    Returns dict: agent_name → { url, card } | { url, error }
    """
    L.banner("STEP 3 · AGENT DISCOVERY  (reads /.well-known/agent.json)")
    agents: Dict[str, Any] = {}

    for name, url in AGENT_URLS.items():
        try:
            headers = tracing.inject_trace_headers()
            card    = http_get(
                f"{name}-agent",
                f"{url}/.well-known/agent.json",
                timeout=5,
                headers=headers,
            ).json()
            agents[name] = {"url": url, "card": card}

            skills    = card.get("skills", [])
            skill_ids = [s.get("id") for s in skills]
            req_tools = []
            for s in skills:
                req_tools.extend(s.get("requires_mcp_tools", []))

            L.row(f"  {name.upper()} ✓ discovered",
                  f"{card.get('name')} v{card.get('version')}")
            L.row(f"    skills",          skill_ids)
            L.row(f"    requires_mcp",    req_tools)
            L.row(f"    mcp_provided_by", card.get("mcp_provided_by", "orchestrator"))
            L.sep()

        except Exception as exc:
            logger.warning("│  %s ✗  FAILED: %s", name.upper(), exc)
            agents[name] = {"url": url, "error": str(exc)}

    L.banner_end()
    return agents
