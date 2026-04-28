import json
import logging
import os
import socket
import threading
import time
from typing import Optional

import requests
from flask import Flask, jsonify

# ── Configuration ────────────────────────────────────────────────────────────

HSL_HOST       = os.getenv("HSL_HOST",       "0.0.0.0")
HSL_TCP_PORT   = int(os.getenv("HSL_TCP_PORT", "9999"))
TEMPO_OTLP_URL = os.getenv("TEMPO_OTLP_URL", "http://tempo:4318/v1/traces")
FLUSH_INTERVAL = float(os.getenv("FLUSH_INTERVAL", "2.0"))
BATCH_SIZE     = int(os.getenv("BATCH_SIZE",     "50"))
LOG_LEVEL      = os.getenv("LOG_LEVEL",          "INFO")

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s | hsl-trace-bridge | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

app   = Flask(__name__)
stats = {"received": 0, "exported": 0, "errors": 0, "no_trace": 0}

_batch_lock = threading.Lock()
_pending: list = []

# ── Helpers ──────────────────────────────────────────────────────────────────

def hex_to_bytes(h: str, expected_chars: int) -> Optional[bytes]:
    try:
        if not h or len(h) != expected_chars:
            return None
        return bytes.fromhex(h)
    except ValueError:
        return None


# ── OTLP Span Builder ────────────────────────────────────────────────────────

def build_span(msg: dict) -> Optional[dict]:
    trace_id_hex  = msg.get("trace_id", "")
    span_id_hex   = msg.get("span_id", "")
    parent_id_hex = msg.get("parent_span_id", "")

    if not trace_id_hex or len(trace_id_hex) != 32:
        stats["no_trace"] += 1
        return None

    trace_id_b  = hex_to_bytes(trace_id_hex, 32)
    span_id_b   = hex_to_bytes(span_id_hex, 16)
    parent_id_b = hex_to_bytes(parent_id_hex, 16) if parent_id_hex else None

    if not trace_id_b or not span_id_b:
        logger.warning(f"Invalid trace/span id: {msg}")
        return None

    ts_ms    = msg.get("timestamp_ms", int(time.time() * 1000))
    total_ms = msg.get("total_ms", 0)
    start_ns = (ts_ms - total_ms) * 1_000_000
    end_ns   = ts_ms * 1_000_000

    method = msg.get("method", "UNKNOWN")
    uri    = msg.get("uri", "/")
    path   = uri.split("?")[0]
    status = int(msg.get("status", 0))

    # OTLP span kind 5 = SPAN_KIND_PROXY
    # Transparent proxy/load balancer — does not add service graph edges,
    # preserving the existing frontend→travel-mcp / frontend→weather-mcp
    # edges from the pod spans.
    span = {
        "traceId":           trace_id_b.hex(),
        "spanId":            span_id_b.hex(),
        "name":              f"{method} {path}",
        "kind":              5,
        "startTimeUnixNano": str(start_ns),
        "endTimeUnixNano":   str(end_ns),
        "status":            {"code": 2, "message": f"HTTP {status}"} if status >= 500 else {"code": 1},
        "attributes": [
            {"key": "http.method",      "value": {"stringValue": method}},
            {"key": "http.url",         "value": {"stringValue": uri}},
            {"key": "http.host",        "value": {"stringValue": msg.get("host", "")}},
            {"key": "http.status_code", "value": {"intValue": status}},
            {"key": "net.peer.ip",      "value": {"stringValue": msg.get("client_ip", "")}},
            {"key": "bigip.vs",         "value": {"stringValue": msg.get("vs", "")}},
            {"key": "bigip.ttfb_ms",    "value": {"intValue": msg.get("ttfb_ms", 0)}},
            {"key": "bigip.total_ms",   "value": {"intValue": msg.get("total_ms", 0)}},
            {"key": "bigip.source",     "value": {"stringValue": "hsl-irule"}},
        ],
    }

    if parent_id_b:
        span["parentSpanId"] = parent_id_b.hex()

    if msg.get("mcp_event"):
        span["attributes"].append(
            {"key": "mcp.event", "value": {"stringValue": msg["mcp_event"]}}
        )
    if msg.get("mcp_session_id"):
        span["attributes"].append(
            {"key": "mcp.session_id", "value": {"stringValue": msg["mcp_session_id"]}}
        )

    return span


def build_otlp_payload(messages: list) -> dict:
    by_service: dict[str, list] = {}
    for msg in messages:
        svc = msg.get("service", "bigip-unknown")
        by_service.setdefault(svc, []).append(msg)

    resource_spans = []
    for service_name, msgs in by_service.items():
        spans = [s for s in (build_span(m) for m in msgs) if s]
        if not spans:
            continue
        resource_spans.append({
            "resource": {
                "attributes": [
                    {"key": "service.name",      "value": {"stringValue": service_name}},
                    {"key": "service.namespace",  "value": {"stringValue": "demo-travel"}},
                    {"key": "telemetry.sdk.name", "value": {"stringValue": "bigip-hsl-bridge"}},
                ]
            },
            "scopeSpans": [{
                "scope": {"name": "bigip.irule", "version": "1.0"},
                "spans": spans,
            }],
        })

    return {"resourceSpans": resource_spans}


# ── OTLP Export ──────────────────────────────────────────────────────────────

def export_to_tempo(batch: list):
    if not batch:
        return
    payload = build_otlp_payload(batch)
    if not payload["resourceSpans"]:
        return
    try:
        resp = requests.post(
            TEMPO_OTLP_URL,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=5.0,
        )
        if resp.status_code in (200, 202, 204):
            stats["exported"] += len(batch)
            logger.info(f"Exported {len(batch)} spans → Tempo")
        else:
            stats["errors"] += 1
            logger.warning(f"Tempo {resp.status_code}: {resp.text[:200]}")
    except requests.exceptions.RequestException as e:
        stats["errors"] += 1
        logger.error(f"Tempo export failed: {e}")


# ── TCP HSL Listener ─────────────────────────────────────────────────────────

def process_hsl_line(data: bytes, addr):
    """Parse and queue a single HSL JSON record."""
    try:
        text = data.decode("utf-8", errors="replace").strip()
        if not text:
            return

        # Strip syslog prefix if BIG-IP adds one: <PRI>TIMESTAMP HOSTNAME MSG
        if text.startswith("<"):
            parts = text.split(" ", 3)
            text = parts[3] if len(parts) == 4 else text

        msg = json.loads(text)
        stats["received"] += 1
        logger.info(
            f"HSL recv | trace_id={msg.get('trace_id', '?')} "
            f"service={msg.get('service', '?')} "
            f"status={msg.get('status', '?')} "
            f"total_ms={msg.get('total_ms', '?')}"
        )

        with _batch_lock:
            _pending.append(msg)

        if len(_pending) >= BATCH_SIZE:
            _flush()

    except json.JSONDecodeError as e:
        logger.warning(f"JSON parse error from {addr}: {e} | raw: {data[:120]}")
    except Exception as e:
        logger.error(f"HSL processing error from {addr}: {e}")


def handle_client(conn: socket.socket, addr):
    """
    Handle a persistent HSL TCP connection from BIG-IP.

    BIG-IP HSL TCP keeps a long-lived connection open and streams JSON
    records separated by newlines. We buffer incoming bytes and process
    complete lines as they arrive. If BIG-IP omits the trailing newline
    (it sometimes does on the last record before a keepalive), we also
    flush the buffer when it looks like a complete JSON object.
    """
    buf = b""
    conn.settimeout(60.0)
    logger.info(f"HSL connection accepted from {addr}")

    try:
        while True:
            try:
                chunk = conn.recv(4096)
            except socket.timeout:
                # Keepalive gap — connection still open, just no data yet
                continue
            except Exception as e:
                logger.warning(f"Recv error from {addr}: {e}")
                break

            if not chunk:
                logger.info(f"HSL connection closed by {addr}")
                break

            buf += chunk

            # Process all complete newline-delimited records
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if line:
                    process_hsl_line(line, addr)

            # Flush buffer if it contains a complete JSON object without \n
            # BIG-IP HSL TCP does not always terminate with a newline
            stripped = buf.strip()
            if stripped.startswith(b"{") and stripped.endswith(b"}"):
                process_hsl_line(stripped, addr)
                buf = b""

    finally:
        conn.close()
        logger.info(f"HSL connection handler exiting for {addr}")


def tcp_listener():
    """Accept incoming HSL TCP connections from BIG-IP (one per VS)."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind((HSL_HOST, HSL_TCP_PORT))
    server.listen(16)
    logger.info(f"TCP HSL listener on {HSL_HOST}:{HSL_TCP_PORT}")

    while True:
        try:
            conn, addr = server.accept()
            threading.Thread(
                target=handle_client,
                args=(conn, addr),
                daemon=True,
                name=f"hsl-client-{addr[0]}:{addr[1]}",
            ).start()
        except Exception as e:
            logger.error(f"TCP accept error: {e}")
            time.sleep(0.5)


# ── Batch Flush Loop ─────────────────────────────────────────────────────────

def _flush():
    global _pending
    with _batch_lock:
        if not _pending:
            return
        batch    = _pending[:]
        _pending = []
    export_to_tempo(batch)


def flush_loop():
    while True:
        time.sleep(FLUSH_INTERVAL)
        _flush()


# ── Flask API ────────────────────────────────────────────────────────────────

@app.route("/health")
def health():
    return jsonify({"status": "ok"})


@app.route("/stats")
def get_stats():
    with _batch_lock:
        pending = len(_pending)
    return jsonify({**stats, "pending": pending, "tempo": TEMPO_OTLP_URL})


@app.route("/flush", methods=["POST"])
def manual_flush():
    _flush()
    return jsonify({"status": "flushed"})


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logger.info(f"hsl-trace-bridge | TCP:{HSL_TCP_PORT} | Tempo:{TEMPO_OTLP_URL}")
    threading.Thread(target=tcp_listener, daemon=True, name="tcp-listener").start()
    threading.Thread(target=flush_loop,   daemon=True, name="flush-loop").start()
    app.run(host="0.0.0.0", port=int(os.getenv("HTTP_PORT", "8080")), debug=False, use_reloader=False)
