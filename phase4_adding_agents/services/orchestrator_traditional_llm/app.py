import json
import logging
import os
import uuid
import tiktoken
from typing import Dict, List, Any

import requests
from flask import Flask, request, jsonify

from shared.logging import configure_logging
from shared.metrics import register_metrics
import shared.tracing as tracing
from shared.tracing import init_tracing

SERVICE_NAME = "orchestrator"

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
LLM_MODEL = os.getenv("LLM_MODEL", "llama3.2:1b")

RAG_BASE = os.getenv("RAG_URL", "http://rag-service:8000")
TRAVEL_AGENT_URL = os.getenv("TRAVEL_AGENT_URL", "http://travel-agent:8001")
FLIGHT_AGENT_URL = os.getenv("FLIGHT_AGENT_URL", "http://flight-agent:8002")
HOTEL_AGENT_URL = os.getenv("HOTEL_AGENT_URL", "http://hotel-agent:8003")
ACTIVITY_AGENT_URL = os.getenv("ACTIVITY_AGENT_URL", "http://activity-agent:8004")
WEATHER_AGENT_URL = os.getenv("WEATHER_AGENT_URL", "http://weather-agent:8005")

TRAVEL_MCP_URL = os.getenv("TRAVEL_MCP_URL", "http://travel-mcp:8000/mcp")
WEATHER_MCP_URL = os.getenv("WEATHER_MCP_URL", "http://weather-mcp:8001/mcp")

MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "3"))
VERBOSE_LOGGING = True

app = Flask(__name__)
configure_logging(SERVICE_NAME)
meter = register_metrics(app, SERVICE_NAME)
init_tracing(app, SERVICE_NAME)

logger = logging.getLogger(SERVICE_NAME)
logger.setLevel(logging.DEBUG)

# Token counter for Llama 3.2
ENCODER = tiktoken.get_encoding("cl100k_base")

def count_tokens(text: str) -> int:
    return len(ENCODER.encode(text))

def _truncate(value, limit=1000):
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        try:
            value = json.dumps(value, ensure_ascii=False)
        except Exception:
            value = str(value)
    else:
        value = str(value)
    return value if len(value) <= limit else value[:limit] + f"... [{len(value)-limit} chars truncated]"

def log_http_request(name: str, method: str, url: str, payload=None, headers=None):
    logger.info("[OUT] %-12s %s %s", name.upper(), method, url)
    if payload is not None:
        logger.info("[OUT] %-12s PAYLOAD: %s", name.upper(), _truncate(payload, 800))
    if headers:
        safe_headers = {k: v for k, v in headers.items() if k.lower() not in {"authorization", "cookie"}}
        logger.debug("[OUT] %-12s HEADERS: %s", name.upper(), safe_headers)

def log_http_response(name: str, resp: requests.Response):
    logger.info("[IN ] %-12s %s %s (%.2fs)", name.upper(), resp.status_code, resp.reason, resp.elapsed.total_seconds())
    try:
        body = resp.json()
        logger.info("[IN ] %-12s RESPONSE: %s", name.upper(), _truncate(body, 1200))
        return body
    except Exception:
        body = resp.text
        logger.info("[IN ] %-12s TEXT: %s", name.upper(), _truncate(body, 1200))
        return body

def http_post_json(name: str, url: str, payload: dict, timeout: int = 60, headers: dict | None = None):
    h = headers or {}
    log_http_request(name, "POST", url, payload, h)
    resp = requests.post(url, json=payload, headers=h, timeout=timeout)
    log_http_response(name, resp)
    resp.raise_for_status()
    return resp

def http_get(name: str, url: str, timeout: int = 10, headers: dict | None = None):
    h = headers or {}
    log_http_request(name, "GET", url, headers=h)
    resp = requests.get(url, headers=h, timeout=timeout)
    log_http_response(name, resp)
    resp.raise_for_status()
    return resp


def call_rag(query: str) -> List[Dict[str, Any]]:
    tracer = tracing.get_tracer(SERVICE_NAME)
    headers = tracing.inject_trace_headers()
    with tracer.start_as_current_span("rag_search"):
        rag_url = RAG_BASE.rstrip('/').rstrip('/search')
        full_url = f"{rag_url}/search"
        logger.info("RAG QUERY: %s (%d tokens)", query, count_tokens(query))
        resp = http_post_json("rag-service", full_url, {"query": query}, timeout=30, headers=headers)
        results = resp.json().get("results", [])  
        logger.info("RAG RESULTS: %d documents found", len(results))
        for i, doc in enumerate(results[:3], 1):
            logger.info("RAG DOC %d: %s... (%.1f tokens)", i, doc.get("content", "")[:100], count_tokens(doc.get("content", "")))
        return results


def discover_agents():
    agents = {}
    agent_configs = [
        ("travel", TRAVEL_AGENT_URL),
        ("flight", FLIGHT_AGENT_URL), 
        ("hotel", HOTEL_AGENT_URL),
        ("activity", ACTIVITY_AGENT_URL),
        ("weather", WEATHER_AGENT_URL),
    ]
    for name, url in agent_configs:
        try:
            tracer = tracing.get_tracer(SERVICE_NAME)
            headers = tracing.inject_trace_headers()
            with tracer.start_as_current_span(f"discover_{name}"):
                card = http_get(f"{name}-agent", f"{url}/.well-known/agent-card", timeout=5, headers=headers).json()
                agents[name] = card
                logger.info("%s AGENT DISCOVERED: %s", name.upper(), card.get("name", "unknown"))
        except Exception as e:
            logger.warning("%s agent discovery failed: %s", name, e)
            agents[name] = {"error": str(e)}
    return agents

def call_weather_mcp(query: str):
    try:
        logger.info("CALLING WEATHER MCP for query: %s", query)
        resp = http_post_json("weather-mcp", WEATHER_MCP_URL, {"tool": "get_weather", "args": {"query": query}}, timeout=20)
        logger.info("WEATHER MCP RESPONSE: %s", resp)
        return resp
    except Exception as e:
        logger.warning("WEATHER MCP failed: %s", e)
        return {"error": str(e)}

def ollama_chat(system_prompt: str, user_prompt: str) -> str:
    tracer = tracing.get_tracer(SERVICE_NAME)
    headers = tracing.inject_trace_headers()
    with tracer.start_as_current_span("ollama_chat"):
        sys_tokens = count_tokens(system_prompt)
        user_tokens = count_tokens(user_prompt)
        total_tokens = sys_tokens + user_tokens
        logger.info("OLLAMA CHAT: system=%d tokens, user=%d tokens, total=%d tokens", sys_tokens, user_tokens, total_tokens)
        logger.debug("SYSTEM PROMPT: %s", _truncate(system_prompt, 2000))
        logger.debug("USER PROMPT: %s", _truncate(user_prompt, 1000))
        
        resp = http_post_json(
            "ollama",
            f"{OLLAMA_URL}/api/chat",
            {
                "model": LLM_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                "stream": False,
                "format": "json",
            },
            timeout=120,
            headers=headers,
        )
        content = resp.json()["message"]["content"]
        output_tokens = count_tokens(content)
        logger.info("OLLAMA RESPONSE: %d chars, %d tokens", len(content), output_tokens)
        logger.info("OLLAMA OUTPUT: %s", _truncate(content, 2000))
        return content

@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}, 200

@app.route("/plan", methods=["POST"])
def plan_trip():
    tracer = tracing.get_tracer(SERVICE_NAME)
    with tracer.start_as_current_span("plan_trip"):
        body = request.get_json(force=True)
        logger.info("=" * 80)
        logger.info("NEW TRIP PLANNING REQUEST")
        logger.info("USER PROMPT: %s (%d tokens)", body.get("prompt", ""), count_tokens(body.get("prompt", "")))
        prompt = body.get("prompt")
        if not prompt:
            return jsonify({"error": "prompt required"}), 400
        
        conversation_id = str(uuid.uuid4())
        logger.info("CONVERSATION ID: %s", conversation_id)
        
        logger.info("STEP 1: Extract user intent")
        user_context = extract_user_context(prompt)
        logger.info("USER CONTEXT: %s", json.dumps(user_context, indent=2))
        
        logger.info("STEP 2: RAG augmentation")
        rag_docs = call_rag(prompt)
        rag_context = "\\n".join([doc.get("content", "")[:500] for doc in rag_docs[:5]])
        logger.info("RAG CONTEXT SUMMARY (%d docs): %s", len(rag_docs), _truncate(rag_context, 1500))
        
        logger.info("STEP 3: Agent discovery")
        agents = discover_agents()
        
        logger.info("STEP 4: MCP augmentation (weather)")
        mcp_data = call_weather_mcp(prompt)
        
        logger.info("STEP 5: Build orchestration context")
        system_prompt = f"""TRAVEL ORCHESTRATOR

USER GOAL: {prompt}

USER CONTEXT:
{json.dumps(user_context, indent=2)}

RAG DOCUMENTS ({len(rag_docs)}):
{rag_context}

AVAILABLE AGENTS:
{json.dumps(agents, indent=2)}

MCP DATA:
{json.dumps(mcp_data, indent=2)}

TASK: Create a complete JSON itinerary. Include:
{{"itinerary": {{"origin": str, "destination": str, "dates": {{}}, "budget": str, "hotels": [], "activities": [], "flights": [], "total_cost": str}}}}

Return ONLY valid JSON."""

        sys_tokens = count_tokens(system_prompt)
        logger.info("FINAL SYSTEM PROMPT: %d tokens", sys_tokens)
        
        logger.info("STEP 6: LLM orchestration")
        llm_response = ollama_chat(system_prompt, prompt)
        
        try:
            itinerary = json.loads(llm_response)
            logger.info("ITINERARY PARSED SUCCESSFULLY")
        except Exception as e:
            logger.warning("LLM response not JSON: %s", e)
            itinerary = {"raw_response": llm_response}
        
        logger.info("=" * 80)
        return jsonify({
            "conversation_id": conversation_id,
            "tokens": {
                "user_prompt": count_tokens(prompt),
                "rag_context": sum(count_tokens(doc.get("content", "")) for doc in rag_docs),
                "system_prompt": sys_tokens,
                "llm_output": count_tokens(llm_response),
            },
            "pipeline": {
                "user_context": user_context,
                "rag_docs_count": len(rag_docs),
                "agents_discovered": len([a for a in agents.values() if "error" not in a]),
                "mcp_data": mcp_data,
            },
            "itinerary": itinerary
        })

def extract_user_context(prompt: str) -> Dict[str, Any]:
    p_lower = prompt.lower()
    context = {
        "preferences": prompt,
        "destination": "Barcelona" if "barcelona" in p_lower else None,
        "budget": "budget" if "budget" in p_lower else None,
        "duration": "4 days" if "4 days" in p_lower else None,
        "season": "spring" if "spring" in p_lower else None,
        "interests": ["nature"] if "nature" in p_lower else [],
    }
    logger.info("CONTEXT EXTRACTION: %s", json.dumps(context, indent=2))
    return context

@app.route("/a2a/route", methods=["POST"])
def a2a_route():
    msg = request.get_json(force=True)
    logger.info("A2A ROUTING: %s", _truncate(msg, 1000))
    return jsonify({"status": "ok"}), 200

if __name__ == "__main__":
    logger.info("ORCHESTRATOR STARTED with model=%s", LLM_MODEL)
    app.run(host="0.0.0.0", port=9000, debug=False)

