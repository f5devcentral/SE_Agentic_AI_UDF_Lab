"""
orchestrator/mcp_gateway.py
────────────────────────────
Central MCP governance layer using the same working FastMCP response handling
pattern as ../phase2_introducing_mcp/frontend/mcp_client.py

v4.0 additions:
  • build_mcp_arguments_for_date()  — like build_mcp_arguments() but accepts
    an explicit departure date override (used by the budget re-search loop)
  • refetch_flights_hotels()        — convenience wrapper called by pipeline.py
    budget loop to re-fetch flights + hotels for a new date, updating mcp_cache
    in-place and returning updated agent_mcp_results slices
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Tuple

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

import log_utils as L
import shared.tracing as tracing
from config import SEASON_DATE, SKILL_MCP_MAP, TRAVEL_MCP_URL

logger = logging.getLogger("orchestrator")


def clear_session_cache():
    logger.info("[MCP] No manual MCP session cache; FastMCP manages sessions internally")


def _run_async(coro):
    return asyncio.run(coro)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "model_dump") and callable(value.model_dump):
        try:
            return _json_safe(value.model_dump(mode="json"))
        except TypeError:
            return _json_safe(value.model_dump())
        except Exception:
            pass
    if hasattr(value, "dict") and callable(value.dict):
        try:
            return _json_safe(value.dict())
        except Exception:
            pass
    if hasattr(value, "root"):
        try:
            return _json_safe(value.root)
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        try:
            return _json_safe(
                {k: v for k, v in vars(value).items() if not k.startswith("_")}
            )
        except Exception:
            pass
    return str(value)


async def _call_mcp_tool_async(
    mcp_url: str,
    tool_name: str,
    arguments: Dict[str, Any],
    trace_context: Dict[str, str] = None,
) -> Any:
    logger.info("[MCP] Calling %s at %s", tool_name, mcp_url)
    logger.debug("[MCP] Arguments: %s", arguments)

    trace_headers = tracing.inject_trace_headers()
    if trace_context:
        trace_headers.update(trace_context)

    meta = {"trace_context": trace_headers} if trace_headers else None

    transport = StreamableHttpTransport(mcp_url)

    async with Client(transport=transport) as client:
        result = await client.call_tool(tool_name, arguments, meta=meta)
        logger.debug("[MCP] Raw result object: %r", result)

        # Modern FastMCP
        structured = getattr(result, "structured_content", None)
        if structured:
            logger.debug("[MCP] structured_content detected")
            structured = _json_safe(structured)
            if isinstance(structured, dict) and "result" in structured:
                return _json_safe(structured["result"])
            return structured

        # Legacy / compatibility path
        if hasattr(result, "content") and result.content:
            first_item = result.content[0]

            if hasattr(first_item, "text"):
                raw_text = first_item.text
                logger.debug("[MCP] TextContent received: %s", raw_text)

                try:
                    parsed = json.loads(raw_text)
                    if isinstance(parsed, dict) and "result" in parsed:
                        return _json_safe(parsed["result"])
                    return _json_safe(parsed)
                except Exception:
                    logger.warning("[MCP] JSON parsing failed, returning raw text")
                    return raw_text

            return _json_safe(result.content)

        # Very old behavior
        if isinstance(result, list) and len(result) > 0:
            first_item = result[0]
            if hasattr(first_item, "text"):
                try:
                    parsed = json.loads(first_item.text)
                    if isinstance(parsed, dict) and "result" in parsed:
                        return _json_safe(parsed["result"])
                    return _json_safe(parsed)
                except Exception:
                    return first_item.text

        logger.warning("[MCP] Unexpected response format, returning raw normalized result")
        return _json_safe(result)


def mcp_initialize(mcp_server: str, mcp_url: str, pipeline_session_id: str) -> str:
    L.banner(f"MCP READY  {mcp_server}")
    L.row("url", mcp_url)
    L.row("pipeline_session_id", pipeline_session_id)
    L.row("note", "FastMCP manages initialize/session internally; tracing passed through meta.trace_context")
    L.banner_end()
    return pipeline_session_id


def mcp_tool_call(
    mcp_server: str,
    mcp_url: str,
    tool: str,
    arguments: Dict[str, Any],
    session_id: str,
) -> Any:
    mcp_initialize(mcp_server, mcp_url, session_id)

    L.banner(f"MCP CALL  {mcp_server} → {tool}")
    L.row("url", mcp_url)
    L.row("tool", tool)
    L.row("pipeline_session_id", session_id)
    L.row("arguments", arguments, 400)

    tracer = tracing.get_tracer("orchestrator")
    with tracer.start_as_current_span(f"mcp_{tool}") as span:
        try:
            trace_ctx = tracing.inject_trace_headers()

            try:
                span.set_attribute("mcp.server", mcp_server)
                span.set_attribute("mcp.url", mcp_url)
                span.set_attribute("mcp.tool.name", tool)
                if "traceparent" in trace_ctx:
                    span.set_attribute("traceparent", trace_ctx["traceparent"])
                if "tracestate" in trace_ctx:
                    span.set_attribute("tracestate", trace_ctx["tracestate"])
            except Exception:
                pass

            result = _run_async(
                _call_mcp_tool_async(
                    mcp_url=mcp_url,
                    tool_name=tool,
                    arguments=arguments,
                    trace_context=trace_ctx,
                )
            )

            result = _json_safe(result)
            L.row("← result", result, 800)
            L.banner_end()
            return result

        except Exception as exc:
            logger.error("[MCP] Tool call failed: %s - %s", tool, exc)
            L.banner_end()
            return {"error": str(exc)}


# ── MCP argument builders ─────────────────────────────────────────────────────

def build_mcp_arguments(tool: str, user_context: Dict[str, Any]) -> Dict[str, Any]:
    """Build MCP call arguments using the season-derived default date."""
    season = user_context.get("season")
    date   = SEASON_DATE.get(season or "", "2025-07-15")
    return build_mcp_arguments_for_date(tool, user_context, date)


def build_mcp_arguments_for_date(
    tool: str,
    user_context: Dict[str, Any],
    date: str,
) -> Dict[str, Any]:
    """
    Build MCP call arguments for a specific departure date.
    Used by both the initial pre-fetch and the budget re-search loop
    when alternate dates are tried.
    """
    dest     = user_context.get("destination", "Unknown")
    origin   = user_context.get("origin",      "Unknown")
    duration = user_context.get("duration")

    if tool == "search_flights":
        return {
            "origin":      origin,
            "destination": dest,
            "date":        date,
        }

    if tool == "search_hotels":
        try:
            # parse "7 days" / "1 week" / "2 weeks" etc.
            parts = (duration or "7 days").lower().split()
            qty   = int(parts[0])
            days  = qty * 7 if "week" in (duration or "") else qty
        except Exception:
            days = 7
        checkout = (
            datetime.strptime(date, "%Y-%m-%d") + timedelta(days=days)
        ).strftime("%Y-%m-%d")
        return {
            "city":     dest,
            "checkin":  date,
            "checkout": checkout,
        }

    if tool == "search_activities":
        return {"city": dest}

    if tool == "get_weather_forecast":
        return {"city": dest, "date": date}

    return {}


# ── Budget-loop re-fetch ──────────────────────────────────────────────────────

def refetch_flights_hotels(
    user_context: Dict[str, Any],
    new_date:     str,
    session_id:   str,
    mcp_cache:    Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Called by the pipeline budget loop when the current flight+hotel combination
    exceeds the user's budget and an alternate departure date must be tried.

    Calls travel-mcp for search_flights and search_hotels using new_date,
    updates mcp_cache in-place, and returns updated mcp_results slices for
    the flight agent and hotel agent respectively.

    Returns:
        (flight_mcp_results, hotel_mcp_results)
        Each is a dict with a single key ("flights" or "hotels").
    """
    reg_flights = SKILL_MCP_MAP["search_flights"]
    reg_hotels  = SKILL_MCP_MAP["search_hotels"]

    # ── re-fetch flights ──────────────────────────────────────────────────────
    L.banner(f"BUDGET LOOP · RE-FETCH  travel-mcp → search_flights  date={new_date}")
    L.row("reason",   "previous flight+hotel total exceeded budget")
    L.row("new_date", new_date)
    args_flights = build_mcp_arguments_for_date("search_flights", user_context, new_date)
    L.row("arguments", args_flights)
    L.banner_end()

    flights = mcp_tool_call(
        mcp_server = reg_flights["mcp_server"],
        mcp_url    = reg_flights["mcp_url"],
        tool       = "search_flights",
        arguments  = args_flights,
        session_id = session_id,
    )
    flights = _json_safe(flights)
    mcp_cache["flights"] = flights
    L.banner("BUDGET LOOP · RE-FETCH RESULT  flights")
    L.row("flights_count", len(flights) if isinstance(flights, list) else "?")
    L.row("flights",       flights, 400)
    L.banner_end()

    # ── re-fetch hotels ───────────────────────────────────────────────────────
    L.banner(f"BUDGET LOOP · RE-FETCH  travel-mcp → search_hotels  date={new_date}")
    L.row("new_date", new_date)
    args_hotels = build_mcp_arguments_for_date("search_hotels", user_context, new_date)
    L.row("arguments", args_hotels)
    L.banner_end()

    hotels = mcp_tool_call(
        mcp_server = reg_hotels["mcp_server"],
        mcp_url    = reg_hotels["mcp_url"],
        tool       = "search_hotels",
        arguments  = args_hotels,
        session_id = session_id,
    )
    hotels = _json_safe(hotels)
    mcp_cache["hotels"] = hotels
    L.banner("BUDGET LOOP · RE-FETCH RESULT  hotels")
    L.row("hotels_count", len(hotels) if isinstance(hotels, list) else "?")
    L.row("hotels",       hotels, 400)
    L.banner_end()

    return {"flights": flights}, {"hotels": hotels}


# ── Governance resolver ───────────────────────────────────────────────────────

def resolve_mcp_for_agent(
    agent_name: str,
    card: Dict[str, Any],
    user_context: Dict[str, Any],
    session_id: str,
    mcp_cache: Dict[str, Any],
) -> Dict[str, Any]:
    L.banner(f"GOVERNANCE · MCP RESOLUTION FOR {agent_name.upper()}")
    L.row("agent", agent_name)
    L.row("mcp_provided_by", card.get("mcp_provided_by", "orchestrator"))

    required_tools = []
    for skill in card.get("skills", []):
        skill_id   = skill.get("id", "?")
        skill_tools = skill.get("requires_mcp_tools", [])
        L.row(f"  skill [{skill_id}]  requires", skill_tools)
        for tool_name in skill_tools:
            if tool_name not in required_tools:
                required_tools.append(tool_name)

    if not required_tools:
        L.row("  → no MCP tools required for this agent")
        L.banner_end()
        return {}

    mcp_results: Dict[str, Any] = {}

    for tool_name in required_tools:
        reg = SKILL_MCP_MAP.get(tool_name)
        if not reg:
            logger.warning("tool '%s' not in SKILL_MCP_MAP", tool_name)
            continue

        result_key = reg["result_key"]

        if result_key in mcp_cache:
            L.row(f"  tool [{tool_name}]", f"→ CACHE HIT  key={result_key}")
            mcp_results[result_key] = mcp_cache[result_key]
            continue

        L.sep()
        L.row(f"  tool [{tool_name}]", f"→ {reg['mcp_server']}  result_key={result_key}")

        arguments = build_mcp_arguments(tool_name, user_context)
        L.row("    arguments", arguments, 400)

        result = mcp_tool_call(
            mcp_server = reg["mcp_server"],
            mcp_url    = reg["mcp_url"],
            tool       = tool_name,
            arguments  = arguments,
            session_id = session_id,
        )

        result = _json_safe(result)

        L.row(f"    ← [{result_key}]", result, 800)
        mcp_results[result_key] = result
        mcp_cache[result_key]   = result

    L.banner_end()
    return mcp_results
