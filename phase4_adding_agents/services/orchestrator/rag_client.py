"""
orchestrator/rag_client.py
──────────────────────────
Calls the RAG service to retrieve destination-relevant documents.
"""

import logging
from typing import Any, Dict, List

import log_utils as L
import shared.tracing as tracing
from config import RAG_BASE, RAG_DOC_CHARS, RAG_DOC_LIMIT
from http_client import http_post_json

logger = logging.getLogger("orchestrator")


def call_rag(query: str) -> List[Dict[str, Any]]:
    """Search the RAG service and return up to RAG_DOC_LIMIT documents."""
    L.banner("STEP 2 · RAG SEARCH")
    L.row("query", query, 300)

    headers = tracing.inject_trace_headers()
    tracer  = tracing.get_tracer("orchestrator")

    with tracer.start_as_current_span("rag_search"):
        try:
            resp    = http_post_json(
                "rag-service",
                f"{RAG_BASE.rstrip('/')}/search",
                {"query": query},
                timeout=20,
                headers=headers,
            )
            results = resp.json().get("results", [])[:RAG_DOC_LIMIT]
            L.row("docs returned", len(results))
            for i, doc in enumerate(results, 1):
                snippet = doc.get("content", "")[:140].replace("\n", " ")
                L.row(f"  doc[{i}]", snippet)
            L.banner_end()
            return results
        except Exception as exc:
            logger.warning("│  RAG FAILED: %s", exc)
            L.banner_end()
            return []


def build_rag_context(docs: List[Dict[str, Any]]) -> str:
    """Serialise RAG docs into a plain-text context string for agents."""
    parts = []
    for i, d in enumerate(docs[:RAG_DOC_LIMIT], 1):
        content = d.get("content", "")
        parts.append(f"DOC {i}: {L.trunc(content, RAG_DOC_CHARS)}")
    return "\n".join(parts)
