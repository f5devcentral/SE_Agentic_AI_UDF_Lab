# BIG-IP Virtual Server Tracing for MCP — Design & Implementation Guide

## Overview

This document covers the full implementation of BIG-IP Virtual Servers in front of the `frontend`, `travel-mcp`, and `weather-mcp` services, with W3C Trace Context propagation, HSL telemetry streaming, and synthetic OTLP span injection into Tempo — without breaking the Tempo service graph.

### Traffic Flow

```
Browser
  └─▶ BIG-IP VS_FRONTEND  ──▶ frontend pod :5000
                                    │
                    ┌───────────────┴───────────────┐
                    ▼                               ▼
          BIG-IP VS_TRAVEL_MCP          BIG-IP VS_WEATHER_MCP
                    │                               │
          travel-mcp pod :8000          weather-mcp pod :8001

Each VS fires HSL UDP ──▶ hsl-trace-bridge pod ──▶ Tempo OTLP HTTP
traceparent passes through BIG-IP unmodified
```

### Tracing Model

BIG-IP cannot natively emit OTLP spans. The approach:

1. iRules extract `traceparent` from every request, generate a BIG-IP `span_id`, record TTFB and total latency, and fire the data via HSL UDP — without touching the header.
2. `hsl-trace-bridge` receives the UDP datagrams, reconstructs valid OTLP spans using the trace context extracted by the iRule, and POSTs them to Tempo.
3. In Tempo, BIG-IP spans appear as **siblings** of the pod spans under the same parent — preserving the service graph edges (frontend → travel-mcp, frontend → weather-mcp) while adding BIG-IP as an observable hop.

### Why Sibling, Not Wrapper

Tempo's service graph is built from `(caller_service, callee_service)` pairs derived from span parent relationships. If the BIG-IP span were injected as the *parent* of the MCP pod span (i.e. the iRule modified `traceparent`), the edge would become `bigip → travel-mcp` instead of `frontend → travel-mcp`, breaking the graph. By leaving `traceparent` untouched and emitting a sibling span (same `parent_span_id` as the pod span), the existing service graph edges are preserved and BIG-IP appears as additional instrumentation on the same call.

```
[frontend]  search_flights outgoing call          parent_span_id = A
    ├── [bigip-travel-mcp-vs]  POST /mcp           parent_span_id = A  ← synthetic
    └── [travel-mcp]  search_flights execution     parent_span_id = A  ← native OTEL
```

---

## Part 1 — iRules

### Design Principles

- One generic iRule for plain HTTP Virtual Servers (`irule_http_tracing`)
- One generic iRule for MCP Virtual Servers (`irule_mcp_tracing`) — same base, adds MCP session tracking and per-VS service naming from destination port
- Both rules: extract `traceparent`, generate a BIG-IP span ID, measure TTFB + total latency, send HSL JSON, **never modify headers**
- TCL variables are connection-scoped (set in `CLIENT_ACCEPTED`, reused across events) to handle the multi-request SSE pattern FastMCP uses

### iRule: `irule_http_tracing` (generic HTTP)

Apply to any plain HTTP Virtual Server (e.g. `VS_FRONTEND`).

```tcl
# ===========================================================================
# irule_http_tracing
# Generic HTTP Virtual Server — W3C Trace Context extraction + HSL telemetry
#
# Behaviour:
#   - Extracts traceparent / tracestate from every request
#   - Generates a BIG-IP span_id (16 hex chars, 8 random bytes)
#   - Records TTFB (HTTP_RESPONSE) and total latency (HTTP_RESPONSE_RELEASE)
#   - Emits structured JSON via HSL UDP to hsl-trace-bridge
#   - NEVER modifies traceparent — trace context passes through untouched
#
# Required objects (create before applying rule):
#   ltm pool  hsl_trace_bridge_pool   (UDP, member <node>:30999)
#
# Customise per-VS by overriding static::service_name in a wrapper iRule,
# or leave as-is — the VS name is captured from [virtual name].
# ===========================================================================

when RULE_INIT {
    set static::hsl_pool "hsl_trace_bridge_pool"
}

when CLIENT_ACCEPTED {
    set hsl [HSL::open -proto UDP -pool $static::hsl_pool]
}

when HTTP_REQUEST {
    # ── Timestamps and request metadata ────────────────────────────────────
    set req_start_ms  [clock clicks -milliseconds]
    set http_method   [HTTP::method]
    set http_uri      [HTTP::uri]
    set http_host     [HTTP::host]
    set client_ip     [IP::client_addr]
    set vs_name       [virtual name]

    # Derive a human-readable service name from the VS name
    # e.g. /Common/VS_FRONTEND → bigip-vs-frontend
    set svc_raw [string tolower [lindex [split $vs_name "/"] end]]
    set service_name  "bigip-[string map {"_" "-"} $svc_raw]"

    # ── W3C Trace Context extraction ────────────────────────────────────────
    set traceparent ""
    set tracestate  ""
    if { [HTTP::header exists "traceparent"] } {
        set traceparent [HTTP::header value "traceparent"]
    }
    if { [HTTP::header exists "tracestate"] } {
        set tracestate [HTTP::header value "tracestate"]
    }

    # Parse traceparent: 00-<trace_id(32)>-<parent_span_id(16)>-<flags(2)>
    set trace_id       ""
    set parent_span_id ""
    set trace_flags    "00"
    if { $traceparent ne "" } {
        set parts [split $traceparent "-"]
        if { [llength $parts] == 4 } {
            set trace_id       [lindex $parts 1]
            set parent_span_id [lindex $parts 2]
            set trace_flags    [lindex $parts 3]
        }
    }

    # ── Generate BIG-IP span ID ─────────────────────────────────────────────
    # 8 random bytes → 16 hex chars. TCL rand() gives [0,1); scale to uint32.
    set span_id [format "%08x%08x" \
        [expr { int(rand() * 0xFFFFFFFF) }] \
        [expr { int(rand() * 0xFFFFFFFF) }]]

    # ── Persist for response events ─────────────────────────────────────────
    # (connection-scoped variables survive across CLIENT_ACCEPTED events)
    set r_traceparent    $traceparent
    set r_trace_id       $trace_id
    set r_parent_span_id $parent_span_id
    set r_span_id        $span_id
    set r_flags          $trace_flags
    set r_service        $service_name
    set r_vs             $vs_name
    set r_method         $http_method
    set r_uri            $http_uri
    set r_host           $http_host
    set r_client         $client_ip
    set r_ttfb_ms        0
    set r_status         0
    set r_content_type   ""

    # ── DO NOT modify traceparent ───────────────────────────────────────────
    # Trace context must pass through to the backend pod unmodified so that
    # the pod's span correctly inherits the upstream parent.
    # BIG-IP emits a sibling span (same parent_span_id) via HSL only.
}

when HTTP_RESPONSE {
    # First byte of response received — TTFB
    set r_ttfb_ms      [expr { [clock clicks -milliseconds] - $req_start_ms }]
    set r_status       [HTTP::status]
    if { [HTTP::header exists "Content-Type"] } {
        set r_content_type [HTTP::header value "Content-Type"]
    }
}

when HTTP_RESPONSE_RELEASE {
    # Last byte of response released to client — total latency
    set total_ms [expr { [clock clicks -milliseconds] - $req_start_ms }]

    # Sanitise strings for JSON embedding
    set j_uri  [string map {"\\" "\\\\" "\"" "\\\""} $r_uri]
    set j_host [string map {"\\" "\\\\" "\"" "\\\""} $r_host]
    set j_ct   [string map {"\\" "\\\\" "\"" "\\\""} $r_content_type]
    set j_tp   [string map {"\\" "\\\\" "\"" "\\\""} $r_traceparent]
    set j_ts   [string map {"\\" "\\\\" "\"" "\\\""} $tracestate]

    set payload [format \
        \
"{\"source\":\"bigip\",\
\"vs\":\"%s\",\
\"service\":\"%s\",\
\"trace_id\":\"%s\",\
\"span_id\":\"%s\",\
\"parent_span_id\":\"%s\",\
\"traceparent\":\"%s\",\
\"tracestate\":\"%s\",\
\"trace_flags\":\"%s\",\
\"client_ip\":\"%s\",\
\"method\":\"%s\",\
\"host\":\"%s\",\
\"uri\":\"%s\",\
\"status\":%s,\
\"content_type\":\"%s\",\
\"ttfb_ms\":%s,\
\"total_ms\":%s,\
\"timestamp_ms\":%s}" \
        $r_vs \
        $r_service \
        $r_trace_id \
        $r_span_id \
        $r_parent_span_id \
        $j_tp \
        $j_ts \
        $r_flags \
        $r_client \
        $r_method \
        $j_host \
        $j_uri \
        $r_status \
        $j_ct \
        $r_ttfb_ms \
        $total_ms \
        [clock clicks -milliseconds] \
    ]

    HSL::send $hsl $payload
}
```

---

### iRule: `irule_mcp_tracing` (MCP Virtual Servers)

Apply to `VS_TRAVEL_MCP` and `VS_WEATHER_MCP`. Identical trace context handling as above; additionally tracks MCP session IDs and discriminates the service name from the destination port so a single rule covers both VSes.

```tcl
# ===========================================================================
# irule_mcp_tracing
# Generic MCP Virtual Server — W3C Trace Context + MCP session tracking
#
# Apply to both VS_TRAVEL_MCP (:8000) and VS_WEATHER_MCP (:8001).
# Service name is derived automatically from destination port.
#
# FastMCP streamable-http session lifecycle per search request:
#   POST /mcp  → 200  (initialise session, returns mcp-session-id header)
#   POST /mcp  → 202  (tool call accepted, SSE stream opens)
#   GET  /mcp  → 200  (SSE: server streams tool result)
#   POST /mcp  → 200  (tool result ACK)
#   DELETE /mcp → 200 (session close)
#
# The rule captures the mcp-session-id from the first 200 response and
# includes it on all subsequent HSL messages for the same connection,
# enabling correlation of the full MCP round-trip in Tempo.
# ===========================================================================

when RULE_INIT {
    set static::hsl_pool "hsl_trace_bridge_pool"
}

when CLIENT_ACCEPTED {
    set hsl [HSL::open -proto UDP -pool $static::hsl_pool]
    # Session ID is connection-scoped — persists across the 5-request lifecycle
    set conn_mcp_session_id ""
}

when HTTP_REQUEST {
    set req_start_ms  [clock clicks -milliseconds]
    set http_method   [HTTP::method]
    set http_uri      [HTTP::uri]
    set http_host     [HTTP::host]
    set client_ip     [IP::client_addr]
    set dst_port      [TCP::local_port]

    # ── Derive service name from destination port ───────────────────────────
    switch $dst_port {
        8000    { set service_name "bigip-travel-mcp-vs"  }
        8001    { set service_name "bigip-weather-mcp-vs" }
        default { set service_name "bigip-mcp-vs-${dst_port}" }
    }
    set vs_name [virtual name]

    # ── W3C Trace Context extraction ────────────────────────────────────────
    set traceparent ""
    set tracestate  ""
    if { [HTTP::header exists "traceparent"] } {
        set traceparent [HTTP::header value "traceparent"]
    }
    if { [HTTP::header exists "tracestate"] } {
        set tracestate [HTTP::header value "tracestate"]
    }

    set trace_id       ""
    set parent_span_id ""
    set trace_flags    "00"
    if { $traceparent ne "" } {
        set parts [split $traceparent "-"]
        if { [llength $parts] == 4 } {
            set trace_id       [lindex $parts 1]
            set parent_span_id [lindex $parts 2]
            set trace_flags    [lindex $parts 3]
        }
    }

    # ── Generate BIG-IP span ID ─────────────────────────────────────────────
    set span_id [format "%08x%08x" \
        [expr { int(rand() * 0xFFFFFFFF) }] \
        [expr { int(rand() * 0xFFFFFFFF) }]]

    # ── Persist for response events ─────────────────────────────────────────
    set r_traceparent    $traceparent
    set r_trace_id       $trace_id
    set r_parent_span_id $parent_span_id
    set r_span_id        $span_id
    set r_flags          $trace_flags
    set r_service        $service_name
    set r_vs             $vs_name
    set r_method         $http_method
    set r_uri            $http_uri
    set r_host           $http_host
    set r_client         $client_ip
    set r_ttfb_ms        0
    set r_status         0

    # ── DO NOT modify traceparent ───────────────────────────────────────────
}

when HTTP_RESPONSE {
    set r_ttfb_ms  [expr { [clock clicks -milliseconds] - $req_start_ms }]
    set r_status   [HTTP::status]

    # Capture MCP session ID from first 200 response on a new connection
    # FastMCP returns mcp-session-id only on the initialisation POST
    if { $conn_mcp_session_id eq "" } {
        if { [HTTP::header exists "mcp-session-id"] } {
            set conn_mcp_session_id [HTTP::header value "mcp-session-id"]
        }
    }
}

when HTTP_RESPONSE_RELEASE {
    set total_ms [expr { [clock clicks -milliseconds] - $req_start_ms }]

    # Classify the MCP request type from method + status for richer spans
    set mcp_event "unknown"
    switch $r_method {
        "POST"   {
            switch $r_status {
                200     { set mcp_event "init_or_ack" }
                202     { set mcp_event "tool_call"   }
                default { set mcp_event "post_${r_status}" }
            }
        }
        "GET"    { set mcp_event "sse_stream"   }
        "DELETE" { set mcp_event "session_close" }
    }

    set j_uri [string map {"\\" "\\\\" "\"" "\\\""} $r_uri]
    set j_host [string map {"\\" "\\\\" "\"" "\\\""} $r_host]
    set j_tp  [string map {"\\" "\\\\" "\"" "\\\""} $r_traceparent]
    set j_ts  [string map {"\\" "\\\\" "\"" "\\\""} $tracestate]
    set j_sid [string map {"\\" "\\\\" "\"" "\\\""} $conn_mcp_session_id]

    set payload [format \
        \
"{\"source\":\"bigip\",\
\"vs\":\"%s\",\
\"service\":\"%s\",\
\"trace_id\":\"%s\",\
\"span_id\":\"%s\",\
\"parent_span_id\":\"%s\",\
\"traceparent\":\"%s\",\
\"tracestate\":\"%s\",\
\"trace_flags\":\"%s\",\
\"client_ip\":\"%s\",\
\"method\":\"%s\",\
\"host\":\"%s\",\
\"uri\":\"%s\",\
\"status\":%s,\
\"mcp_event\":\"%s\",\
\"mcp_session_id\":\"%s\",\
\"ttfb_ms\":%s,\
\"total_ms\":%s,\
\"timestamp_ms\":%s}" \
        $r_vs \
        $r_service \
        $r_trace_id \
        $r_span_id \
        $r_parent_span_id \
        $j_tp \
        $j_ts \
        $r_flags \
        $r_client \
        $r_method \
        $j_host \
        $j_uri \
        $r_status \
        $mcp_event \
        $j_sid \
        $r_ttfb_ms \
        $total_ms \
        [clock clicks -milliseconds] \
    ]

    HSL::send $hsl $payload
}
```

---

## Part 2 — BIG-IP Configuration

### HSL Pool

The HSL pool must be UDP and point at the `hsl-trace-bridge` NodePort (30999).

```bash
# Node running hsl-trace-bridge — tools node in this lab
tmsh create ltm node hsl_bridge_node address 10.1.20.31

# UDP pool — no health monitor (UDP has no handshake to monitor)
tmsh create ltm pool hsl_trace_bridge_pool \
    members add { 10.1.20.31:30999 { address 10.1.20.31 } } \
    monitor none
```

### Backend Pools (NodePorts)

```bash
tmsh create ltm pool frontend_pool \
    members add { 10.1.20.31:30082 { address 10.1.20.31 } } \
    monitor http

tmsh create ltm pool travel_mcp_pool \
    members add { 10.1.20.31:30100 { address 10.1.20.31 } } \
    monitor http

tmsh create ltm pool weather_mcp_pool \
    members add { 10.1.20.31:30101 { address 10.1.20.31 } } \
    monitor http
```

### Virtual Servers

Replace VIP placeholders with actual BIG-IP VIP addresses.

```bash
# Frontend
tmsh create ltm virtual VS_FRONTEND \
    destination 10.1.10.10:80 \
    ip-protocol tcp \
    profiles add { http { } tcp { } } \
    pool frontend_pool \
    rules { irule_http_tracing }

# Travel MCP
tmsh create ltm virtual VS_TRAVEL_MCP \
    destination 10.1.10.11:8000 \
    ip-protocol tcp \
    profiles add { http { } tcp { } } \
    pool travel_mcp_pool \
    rules { irule_mcp_tracing }

# Weather MCP
tmsh create ltm virtual VS_WEATHER_MCP \
    destination 10.1.10.12:8001 \
    ip-protocol tcp \
    profiles add { http { } tcp { } } \
    pool weather_mcp_pool \
    rules { irule_mcp_tracing }

tmsh save sys config
```

### Upload iRules via iControl REST

```bash
BIGIP="10.1.1.245"
CREDS="admin:password"

# irule_http_tracing
curl -sk -u "$CREDS" -X POST \
  "https://${BIGIP}/mgmt/tm/ltm/rule" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"irule_http_tracing\",
    \"partition\": \"Common\",
    \"apiAnonymous\": $(python3 -c 'import sys,json; print(json.dumps(open("irule_http_tracing.tcl").read()))')
  }"

# irule_mcp_tracing
curl -sk -u "$CREDS" -X POST \
  "https://${BIGIP}/mgmt/tm/ltm/rule" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"irule_mcp_tracing\",
    \"partition\": \"Common\",
    \"apiAnonymous\": $(python3 -c 'import sys,json; print(json.dumps(open("irule_mcp_tracing.tcl").read()))')
  }"
```

---

## Part 3 — Kubernetes: ExternalName Services

The `travel-mcp` and `weather-mcp` ClusterIP services are replaced with headless services + manual Endpoints pointing at the BIG-IP VIPs. The `frontend` service remains a standard NodePort — traffic to BIG-IP's frontend VIP comes from outside the cluster.

> **Note:** Standard `ExternalName` services require a resolvable DNS hostname. The headless + Endpoints pattern works with raw IPs and is recommended here.

```yaml
# k8s/external-services.yaml
# Replace 10.1.10.11 / 10.1.10.12 with actual BIG-IP VIP addresses before applying.

---
apiVersion: v1
kind: Service
metadata:
  name: travel-mcp
  namespace: demo-travel
  annotations:
    description: "Headless svc → BIG-IP VS_TRAVEL_MCP VIP"
spec:
  type: ClusterIP
  clusterIP: None
  ports:
    - name: mcp
      port: 8000
      targetPort: 8000
      protocol: TCP

---
apiVersion: v1
kind: Endpoints
metadata:
  name: travel-mcp
  namespace: demo-travel
subsets:
  - addresses:
      - ip: 10.1.10.11        # ← BIG-IP VS_TRAVEL_MCP VIP
    ports:
      - name: mcp
        port: 8000
        protocol: TCP

---
apiVersion: v1
kind: Service
metadata:
  name: weather-mcp
  namespace: demo-travel
  annotations:
    description: "Headless svc → BIG-IP VS_WEATHER_MCP VIP"
spec:
  type: ClusterIP
  clusterIP: None
  ports:
    - name: mcp
      port: 8001
      targetPort: 8001
      protocol: TCP

---
apiVersion: v1
kind: Endpoints
metadata:
  name: weather-mcp
  namespace: demo-travel
subsets:
  - addresses:
      - ip: 10.1.10.12        # ← BIG-IP VS_WEATHER_MCP VIP
    ports:
      - name: mcp
        port: 8001
        protocol: TCP
```

Apply:

```bash
# Edit VIPs first, then:
kubectl apply -f k8s/external-services.yaml -n demo-travel

# Verify DNS resolves to BIG-IP VIPs
kubectl run dns-check --rm -it --restart=Never \
  --image=busybox -n demo-travel \
  -- nslookup travel-mcp
# Expected: Address 1: 10.1.10.11
```

---

## Part 4 — hsl-trace-bridge

### What It Does

Receives HSL UDP datagrams → reconstructs OTLP spans → POST to Tempo OTLP HTTP.

Key design points:
- Strips syslog prefix (`<PRI>TIMESTAMP HOSTNAME`) if BIG-IP sends one
- Skips spans with no `trace_id` (unsampled traffic — no traceparent in request)
- Emits `SPAN_KIND_PROXY` (kind=5) so Tempo's service graph correctly models BIG-IP as a pass-through intermediary, not a client or server
- Groups spans into `ResourceSpans` by service name (one resource per BIG-IP VS)
- Batches up to 50 spans or flushes every 2 seconds

### `app.py`

```python
"""
hsl-trace-bridge
================
Receives HSL UDP datagrams from BIG-IP iRules.
Reconstructs OTLP spans that slot into the existing trace tree.
Forwards to Tempo via OTLP HTTP /v1/traces.

Span placement in Tempo:
  trace_id       = from W3C traceparent (same trace as pods)
  span_id        = BIG-IP-generated span_id from iRule
  parent_span_id = parent_span_id from traceparent (upstream pod's span)
  kind           = SPAN_KIND_PROXY (5) — preserves Tempo service graph
  service.name   = "bigip-travel-mcp-vs" etc.
"""

import json
import logging
import os
import socket
import struct
import threading
import time
from typing import Optional

import requests
from flask import Flask, jsonify

# ── Configuration ────────────────────────────────────────────────────────────

HSL_UDP_HOST   = os.getenv("HSL_UDP_HOST",   "0.0.0.0")
HSL_UDP_PORT   = int(os.getenv("HSL_UDP_PORT", "9999"))
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

    method   = msg.get("method", "UNKNOWN")
    uri      = msg.get("uri", "/")
    path     = uri.split("?")[0]
    status   = int(msg.get("status", 0))

    # OTLP span kind 5 = SPAN_KIND_PROXY
    # This is the correct kind for a transparent proxy/load balancer.
    # Tempo uses kind to determine service graph edge direction:
    #   PROXY → neither adds a client nor server edge, preserving the
    #   existing frontend→travel-mcp edge from the pod spans.
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

    # MCP-specific attributes
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
            logger.debug(f"Exported {len(batch)} spans → Tempo")
        else:
            stats["errors"] += 1
            logger.warning(f"Tempo {resp.status_code}: {resp.text[:200]}")
    except requests.exceptions.RequestException as e:
        stats["errors"] += 1
        logger.error(f"Tempo export failed: {e}")


# ── UDP Listener ─────────────────────────────────────────────────────────────

def udp_listener():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((HSL_UDP_HOST, HSL_UDP_PORT))
    sock.settimeout(1.0)
    logger.info(f"UDP HSL listener on {HSL_UDP_HOST}:{HSL_UDP_PORT}")

    while True:
        try:
            data, _ = sock.recvfrom(65535)
        except socket.timeout:
            continue
        except Exception as e:
            logger.error(f"UDP recv error: {e}")
            continue

        try:
            text = data.decode("utf-8", errors="replace").strip()
            # Strip syslog prefix if present: <PRI>TIMESTAMP HOSTNAME MSG
            if text.startswith("<"):
                parts = text.split(" ", 3)
                text = parts[3] if len(parts) == 4 else text

            msg = json.loads(text)
            stats["received"] += 1

            with _batch_lock:
                _pending.append(msg)

            if len(_pending) >= BATCH_SIZE:
                _flush()

        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse error: {e} | raw: {data[:120]}")
        except Exception as e:
            logger.error(f"HSL processing error: {e}")


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
    logger.info(f"hsl-trace-bridge | UDP:{HSL_UDP_PORT} | Tempo:{TEMPO_OTLP_URL}")
    threading.Thread(target=udp_listener, daemon=True, name="udp").start()
    threading.Thread(target=flush_loop,   daemon=True, name="flush").start()
    app.run(host="0.0.0.0", port=int(os.getenv("HTTP_PORT", "8080")), debug=False)
```

### `Dockerfile`

```dockerfile
FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .

EXPOSE 9999/udp
EXPOSE 8080/tcp

ENV PYTHONUNBUFFERED=1

HEALTHCHECK --interval=15s --timeout=3s \
  CMD curl -f http://localhost:8080/health || exit 1

CMD ["python", "app.py"]
```

### `requirements.txt`

```
flask>=3.0.0
requests>=2.31.0
```

### `k8s/hsl-trace-bridge.yaml`

```yaml
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: hsl-trace-bridge
  namespace: demo-travel
spec:
  replicas: 1
  selector:
    matchLabels:
      app: hsl-trace-bridge
  template:
    metadata:
      labels:
        app: hsl-trace-bridge
    spec:
      containers:
        - name: hsl-trace-bridge
          image: 10.1.1.7:30500/demo-travel/hsl-trace-bridge:latest
          imagePullPolicy: Always
          ports:
            - name: udp-hsl
              containerPort: 9999
              protocol: UDP
            - name: http-api
              containerPort: 8080
              protocol: TCP
          env:
            - name: HSL_UDP_PORT
              value: "9999"
            - name: HTTP_PORT
              value: "8080"
            - name: TEMPO_OTLP_URL
              value: "http://TEMPO_HOST:4318/v1/traces"   # ← replace
            - name: FLUSH_INTERVAL
              value: "2.0"
            - name: BATCH_SIZE
              value: "50"
            - name: LOG_LEVEL
              value: "INFO"
          livenessProbe:
            httpGet: { path: /health, port: 8080 }
            initialDelaySeconds: 5
            periodSeconds: 15
          readinessProbe:
            httpGet: { path: /health, port: 8080 }
            initialDelaySeconds: 3
            periodSeconds: 10
          resources:
            requests: { cpu: "50m",  memory: "64Mi"  }
            limits:   { cpu: "200m", memory: "128Mi" }

---
apiVersion: v1
kind: Service
metadata:
  name: hsl-trace-bridge
  namespace: demo-travel
spec:
  selector:
    app: hsl-trace-bridge
  type: NodePort
  ports:
    - name: udp-hsl
      port: 9999
      targetPort: 9999
      protocol: UDP
      nodePort: 30999    # BIG-IP HSL pool → <node>:30999
    - name: http-api
      port: 8080
      targetPort: 8080
      protocol: TCP
      nodePort: 30998    # curl http://<node>:30998/stats
```

---

## Part 5 — Build and Deploy

```bash
# 1. Build and push
docker build --no-cache \
  -t 10.1.1.7:30500/demo-travel/hsl-trace-bridge:latest \
  ./hsl-trace-bridge/

docker push 10.1.1.7:30500/demo-travel/hsl-trace-bridge:latest

# 2. Set Tempo URL in the manifest, then deploy
kubectl apply -f k8s/hsl-trace-bridge.yaml

kubectl rollout status deployment/hsl-trace-bridge -n demo-travel

# 3. Verify UDP listener is reachable from tools node
echo '{"source":"test","trace_id":"","span_id":"","service":"test"}' \
  | nc -u -w1 10.1.20.31 30999

curl -s http://10.1.20.31:30998/stats | python3 -m json.tool

# 4. Apply ExternalName services (after editing VIPs)
kubectl apply -f k8s/external-services.yaml

# 5. Confirm DNS from frontend pod resolves to BIG-IP VIPs
kubectl exec -it deployment/frontend -n demo-travel -- \
  python3 -c "import socket; print(socket.getaddrinfo('travel-mcp', 8000))"
```

---

## Part 6 — Tempo Configuration

Ensure Tempo has OTLP HTTP enabled and service graph processing active.

```yaml
# tempo.yaml (relevant sections)
server:
  http_listen_port: 3200

distributor:
  receivers:
    otlp:
      protocols:
        http:
          endpoint: 0.0.0.0:4318   # hsl-trace-bridge POSTs here
        grpc:
          endpoint: 0.0.0.0:4317   # pods export here

metrics_generator:
  registry:
    external_labels:
      source: tempo
      cluster: demo-travel
  storage:
    path: /tmp/tempo/generator/wal
  processor:
    service_graphs:
      # Include BIG-IP proxy spans in the graph without creating new edges
      # SPAN_KIND_PROXY spans are transparent — they inherit parent/child
      # relationships from the existing trace tree
      dimensions:
        - bigip.vs
    span_metrics:
      dimensions:
        - bigip.vs
        - mcp.event

storage:
  trace:
    backend: local
    local:
      path: /tmp/tempo/blocks
```

---

## Part 7 — Verification

### Expected Span Tree in Tempo

After a search request, a single trace should contain:

```
[frontend]  POST /  (Flask auto-instrumented)
  ├── [frontend]  search_flights outgoing HTTP call
  │     ├── [bigip-travel-mcp-vs]  POST /mcp  kind=PROXY  ← BIG-IP synthetic span
  │     └── [travel-mcp]  search_flights  kind=SERVER     ← native OTEL span
  ├── [frontend]  search_hotels outgoing HTTP call
  │     ├── [bigip-travel-mcp-vs]  POST /mcp  kind=PROXY
  │     └── [travel-mcp]  search_hotels  kind=SERVER
  └── [frontend]  get_weather_forecast outgoing HTTP call
        ├── [bigip-weather-mcp-vs]  POST /mcp  kind=PROXY
        └── [weather-mcp]  get_weather_forecast  kind=SERVER
```

### Service Graph in Tempo

Should show edges:

```
frontend ──▶ travel-mcp
frontend ──▶ weather-mcp
```

BIG-IP does **not** appear as a node on the graph — `SPAN_KIND_PROXY` spans are transparent to Tempo's service graph processor. BIG-IP latency is visible per-span in the trace view via `bigip.ttfb_ms` and `bigip.total_ms` attributes.

### Stats Check

```bash
# After a few searches:
curl -s http://10.1.20.31:30998/stats | python3 -m json.tool

# Expected output:
# {
#   "received":  15,      ← HSL datagrams received (5 per MCP session × 2 VSes + frontend)
#   "exported":  15,      ← spans forwarded to Tempo
#   "errors":     0,      ← Tempo POST failures
#   "no_trace":   0,      ← requests without traceparent (should be 0)
#   "pending":    0
# }
```

If `no_trace` is high, the frontend pod is not propagating `traceparent` on its outgoing MCP calls — check that `RequestsInstrumentor` is active in `shared/tracing.py`.

---

## Appendix: HSL JSON Schema

Each iRule emits one JSON object per completed HTTP transaction. Fields common to both iRules:

| Field | Type | Description |
|---|---|---|
| `source` | string | Always `"bigip"` |
| `vs` | string | Full VS name e.g. `/Common/VS_TRAVEL_MCP` |
| `service` | string | Derived service label e.g. `bigip-travel-mcp-vs` |
| `trace_id` | string | 32-char hex from `traceparent` (empty if no header) |
| `span_id` | string | 16-char hex generated by the iRule for this BIG-IP hop |
| `parent_span_id` | string | 16-char hex from `traceparent` — the upstream pod's span |
| `traceparent` | string | Full original `traceparent` header value |
| `tracestate` | string | Full original `tracestate` header value |
| `trace_flags` | string | 2-char hex flags from `traceparent` |
| `client_ip` | string | Source IP of the HTTP client |
| `method` | string | HTTP method |
| `host` | string | HTTP Host header |
| `uri` | string | Full request URI including query string |
| `status` | int | HTTP response status code |
| `ttfb_ms` | int | Time to first byte (ms) |
| `total_ms` | int | Total request latency (ms) |
| `timestamp_ms` | int | Unix epoch ms at response release |

MCP iRule additional fields:

| Field | Type | Description |
|---|---|---|
| `mcp_event` | string | `init_or_ack`, `tool_call`, `sse_stream`, `session_close` |
| `mcp_session_id` | string | FastMCP session ID captured from response header |
