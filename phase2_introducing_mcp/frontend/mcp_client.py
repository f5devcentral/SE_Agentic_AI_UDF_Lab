"""
MCP Client for Frontend
=======================
Synchronous wrapper around FastMCP async client for use in Flask routes.
Compatible with modern FastMCP (CallToolResult) and legacy responses.
"""

import asyncio
import logging
import json
from typing import Any, Dict, List

from fastmcp import Client
from fastmcp.client.transports import StreamableHttpTransport

from shared.tracing import inject_trace_headers

logger = logging.getLogger(__name__)


class MCPClientError(Exception):
    pass


# ──────────────────────────────────────────────────────────────
# Core Async MCP Call
# ──────────────────────────────────────────────────────────────

async def _call_mcp_tool_async(
    mcp_url: str,
    tool_name: str,
    arguments: Dict[str, Any],
    trace_context: Dict[str, str] = None
) -> Any:

    logger.info(f"[MCP] Calling {tool_name} at {mcp_url}")
    logger.debug(f"[MCP] Arguments: {arguments}")

    # Build trace metadata to pass via meta= (headers= not supported in FastMCP 3.x)
    trace_headers = inject_trace_headers()
    if trace_context:
        trace_headers.update(trace_context)

    meta = {"trace_context": trace_headers} if trace_headers else None

    try:
        transport = StreamableHttpTransport(mcp_url)

        async with Client(transport=transport) as client:
            result = await client.call_tool(tool_name, arguments, meta=meta)

            logger.debug(f"[MCP] Raw result object: {result}")

            # ─────────────────────────────────────────────
            # Modern FastMCP: CallToolResult
            # ─────────────────────────────────────────────
            if hasattr(result, "structured_content") and result.structured_content:
                logger.debug("[MCP] structured_content detected")

                structured = result.structured_content

                # Common pattern: {"result": [...]}
                if isinstance(structured, dict):
                    if "result" in structured:
                        return structured["result"]
                    return structured

                return structured

            # ─────────────────────────────────────────────
            # Legacy behavior: list of TextContent
            # ─────────────────────────────────────────────
            if hasattr(result, "content") and result.content:
                first_item = result.content[0]

                if hasattr(first_item, "text"):
                    raw_text = first_item.text
                    logger.debug(f"[MCP] TextContent received: {raw_text}")

                    try:
                        parsed = json.loads(raw_text)
                        # Unwrap {"result": [...]} if present
                        if isinstance(parsed, dict) and "result" in parsed:
                            return parsed["result"]
                        return parsed
                    except Exception:
                        logger.warning("[MCP] JSON parsing failed, returning raw text")
                        return raw_text

            # ─────────────────────────────────────────────
            # Very old behavior: direct list
            # ─────────────────────────────────────────────
            if isinstance(result, list) and len(result) > 0:
                first_item = result[0]
                if hasattr(first_item, "text"):
                    try:
                        return json.loads(first_item.text)
                    except Exception:
                        return first_item.text

            logger.warning("[MCP] Unexpected response format, returning raw result")
            return result

    except Exception as e:
        logger.error(f"[MCP] Tool call failed: {tool_name} - {e}")
        raise MCPClientError(str(e))


# ──────────────────────────────────────────────────────────────
# Sync Wrapper — use asyncio.run() (safe in Python 3.10+)
# ──────────────────────────────────────────────────────────────

def call_mcp_tool(
    mcp_url: str,
    tool_name: str,
    arguments: Dict[str, Any],
    trace_context: Dict[str, str] = None
) -> Any:
    try:
        return asyncio.run(
            _call_mcp_tool_async(mcp_url, tool_name, arguments, trace_context)
        )
    except Exception as e:
        logger.error(f"[MCP] Sync call failed: {e}")
        raise


# ──────────────────────────────────────────────────────────────
# Travel MCP Convenience Functions
# ──────────────────────────────────────────────────────────────

def search_flights(
    mcp_url: str,
    origin: str,
    destination: str,
    date: str,
    trace_context=None
) -> List[Dict[str, Any]]:
    return call_mcp_tool(
        mcp_url,
        "search_flights",
        {
            "origin": origin,
            "destination": destination,
            "date": date
        },
        trace_context
    )


def search_hotels(
    mcp_url: str,
    city: str,
    checkin: str,
    checkout: str,
    trace_context=None
) -> List[Dict[str, Any]]:
    return call_mcp_tool(
        mcp_url,
        "search_hotels",
        {
            "city": city,
            "checkin": checkin,
            "checkout": checkout
        },
        trace_context
    )


def search_activities(
    mcp_url: str,
    city: str,
    trace_context=None
) -> List[Dict[str, Any]]:
    return call_mcp_tool(
        mcp_url,
        "search_activities",
        {"city": city},
        trace_context
    )


# ──────────────────────────────────────────────────────────────
# Weather MCP
# ──────────────────────────────────────────────────────────────

def get_weather_forecast(
    mcp_url: str,
    city: str,
    date: str,
    trace_context=None
) -> Dict[str, Any]:
    return call_mcp_tool(
        mcp_url,
        "get_weather_forecast",
        {
            "city": city,
            "date": date
        },
        trace_context
    )


# ──────────────────────────────────────────────────────────────
# MCP Health Check
# ──────────────────────────────────────────────────────────────

async def _check_mcp_server_async(mcp_url: str) -> bool:
    try:
        transport = StreamableHttpTransport(mcp_url)
        async with Client(transport=transport) as client:
            tools = await client.list_tools()
            return len(tools) > 0
    except Exception as e:
        logger.error(f"[MCP] Server check failed for {mcp_url}: {e}")
        return False


def check_mcp_server(mcp_url: str) -> bool:
    try:
        return asyncio.run(_check_mcp_server_async(mcp_url))
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────
# Tool Discovery
# ──────────────────────────────────────────────────────────────

async def _list_mcp_tools_async(mcp_url: str) -> List[Dict[str, Any]]:
    try:
        transport = StreamableHttpTransport(mcp_url)
        async with Client(transport=transport) as client:
            tools = await client.list_tools()
            return [
                {"name": tool.name, "description": tool.description}
                for tool in tools
            ]
    except Exception as e:
        logger.error(f"[MCP] Failed to list tools from {mcp_url}: {e}")
        return []


def list_mcp_tools(mcp_url: str, trace_context: Dict[str, str] = None) -> List[Dict[str, Any]]:
    try:
        return asyncio.run(_list_mcp_tools_async(mcp_url))
    except Exception:
        return []
