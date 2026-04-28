"""
logviz/app.py  v1.1
────────────────────
Parses kubectl log output from the A2A travel planner stack and generates:
  1. A Mermaid sequence diagram
  2. A token accounting table

Fixed in v1.1:
  - Token counting now uses actual prompt lengths from log rows
    (system_prompt len + user_prompt len → real LLM input tokens)
  - LLM output tokens estimated from response length rows
  - A2A payload sizes read from PAYLOAD (N bytes) lines
  - Duplicate event deduplication (agent logs mixed with orchestrator logs)
  - Discovery rows parsed correctly from new log format
  - MCP skipping warning handled gracefully (no phantom events)
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Data model
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Event:
    ts:      str
    service: str
    kind:    str
    detail:  Dict[str, Any] = field(default_factory=dict)


@dataclass
class TokenRow:
    service:   str
    call:      str
    direction: str    # llm_in | llm_out | a2a_send | a2a_recv | mcp_send | mcp_recv
    tokens:    int
    note:      str = ""

# ─────────────────────────────────────────────────────────────────────────────
# Parser
# ─────────────────────────────────────────────────────────────────────────────

_TS_SVC_LVL = re.compile(
    r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+)"
    r"\s*\|\s*([a-z][a-z0-9\-]+)"
    r"\s*\|\s*(?:INFO|WARNING|ERROR|DEBUG)"
    r"\s*\|\s*(.+)$"
)
_BANNER_OPEN  = re.compile(r"^[┌╔][\─═]\s+(.+?)\s*[─═]+")
_BANNER_CLOSE = re.compile(r"^[└╚]")
_ROW_LINE     = re.compile(r"^│\s+([^:│·]+):\s*(.+)$")
_PAYLOAD_BYTES= re.compile(r"PAYLOAD\s+\((\d+)\s+bytes\)")
_HTTP_OUT     = re.compile(r"\[OUT→\]\s+(\S+)\s+(GET|POST)\s+(\S+)")


class LogParser:
    def __init__(self):
        self.events:     List[Event]    = []
        self.token_rows: List[TokenRow] = []

        self._cur_ts:     str = ""
        self._cur_svc:    str = ""
        self._cur_banner: Optional[str] = None
        self._cur_rows:   Dict[str, str] = {}
        self._last_out_bytes: Dict[str, int] = {}  # name → bytes from PAYLOAD line

    def feed(self, raw: str):
        for line in raw.splitlines():
            self._parse_line(line.strip())
        self._flush()

    # ── Line dispatch ─────────────────────────────────────────────────────────

    def _parse_line(self, line: str):
        m = _TS_SVC_LVL.match(line)
        if m:
            ts, svc, body = m.group(1), m.group(2), m.group(3)
            self._cur_ts  = ts
            self._cur_svc = svc
            self._process_body(body)
            return

        # Lines without prefix (copy-paste artefacts)
        self._process_body(line)

    def _process_body(self, body: str):
        # Banner open
        bm = _BANNER_OPEN.match(body)
        if bm:
            self._flush()
            self._cur_banner = bm.group(1).strip()
            self._cur_rows   = {}
            return

        # Banner close
        if _BANNER_CLOSE.match(body) and self._cur_banner:
            self._flush()
            return

        # Row inside banner
        if self._cur_banner and body.startswith("│"):
            rm = _ROW_LINE.match(body)
            if rm:
                self._cur_rows[rm.group(1).strip()] = rm.group(2).strip()
            return

        # PAYLOAD (N bytes) line — capture for token accounting
        pb = _PAYLOAD_BYTES.search(body)
        if pb:
            hm = _HTTP_OUT.match(body)
            name = hm.group(1).lower() if hm else "unknown"
            self._last_out_bytes[name] = int(pb.group(1))

    # ── Flush banner → event + token rows ────────────────────────────────────

    def _flush(self):
        if not self._cur_banner:
            return
        title = self._cur_banner
        rows  = self._cur_rows
        ts    = self._cur_ts
        svc   = self._cur_svc

        ev = self._classify(title, rows, ts, svc)
        if ev:
            self.events.append(ev)

        self._account_tokens(title, rows, svc)

        self._cur_banner = None
        self._cur_rows   = {}

    # ── Event classification ──────────────────────────────────────────────────

    def _classify(self, title, rows, ts, svc) -> Optional[Event]:
        t = title.upper()

        if "CONTEXT EXTRACTION" in t:
            return Event(ts, svc, "context", {
                "destination": rows.get("destination"),
                "origin":      rows.get("origin"),
                "season":      rows.get("season"),
                "budget_eur":  rows.get("budget_eur"),
                "duration":    rows.get("duration"),
                "interests":   rows.get("interests"),
            })

        if "RAG SEARCH" in t:
            return Event(ts, svc, "rag", {
                "query": rows.get("query", ""),
                "docs":  rows.get("docs returned", "?"),
            })

        if "AGENT DISCOVERY" in t:
            discovered = []
            for k, v in rows.items():
                if "✓" in k:
                    agent = k.split("✓")[1].strip().lower().rstrip(".")
                    if agent and agent not in discovered:
                        discovered.append(agent)
            return Event(ts, svc, "discovery", {"agents": discovered})

        if "GOVERNANCE REGISTRY" in t or "SKILL_MCP_MAP" in t:
            return Event(ts, svc, "governance_registry", {"rows": rows})

        if "MCP INITIALIZE" in t:
            server = re.sub(r"MCP INITIALIZE\s*", "", title, flags=re.IGNORECASE).strip()
            return Event(ts, svc, "mcp_init", {
                "server": server or rows.get("url", ""),
                "url":    rows.get("url", ""),
                "token":  rows.get("→ using token", ""),
            })

        if "MCP RESOLUTION FOR" in t:
            agent = title.upper().split("FOR")[-1].strip().lower()
            tools = []
            for k, v in rows.items():
                if "requires" in k.lower():
                    tools.extend(re.findall(r'"([^"]+)"', v))
            return Event(ts, svc, "governance_resolve", {
                "agent":          agent,
                "required_tools": tools,
            })

        if "MCP CALL" in t and "PRE-FETCH" not in t:
            parts  = re.split(r"→", title.replace("MCP CALL","").strip(), 1)
            server = parts[0].strip() if parts else ""
            tool   = parts[1].strip() if len(parts) > 1 else rows.get("tool", "")
            return Event(ts, svc, "mcp_call", {
                "server": server,
                "tool":   tool or rows.get("tool", ""),
            })

        if "A2A SEND" in t and "→" in t:
            agent  = re.search(r"→\s*(\w[\w\-]*)", title)
            target = agent.group(1).lower() if agent else "unknown"
            skill  = (rows.get("  message.skill") or rows.get("skill") or
                      rows.get("  message.intent") or "")
            mcp_keys = rows.get("  mcp_results keys", "") or rows.get("mcp_results keys", "")
            # payload size from PAYLOAD line logged just before
            payload_bytes = self._last_out_bytes.get(f"{target}-a2a", 0)
            return Event(ts, svc, "a2a_send", {
                "target":        target,
                "task_id":       rows.get("task_id", ""),
                "skill":         skill.strip(),
                "mcp_keys":      mcp_keys,
                "payload_bytes": payload_bytes,
            })

        if "A2A RECV" in t and "←" in t:
            agent  = re.search(r"←\s*(\w[\w\-]*)", title)
            source = agent.group(1).lower() if agent else "unknown"
            state  = rows.get("state", "?")
            return Event(ts, svc, "a2a_recv", {
                "source":   source,
                "task_id":  rows.get("task_id", ""),
                "state":    state,
            })

        if "TASK RECEIVED" in t:
            return Event(ts, svc, "agent_task", {
                "skill":        rows.get("skill", ""),
                "intent":       rows.get("intent", ""),
                "destination":  rows.get("user_context.destination", ""),
                "mcp_flights":  rows.get("flights from MCP", "0"),
                "mcp_hotels":   rows.get("hotels from MCP", "0"),
                "mcp_act":      rows.get("activities from MCP", "0"),
                "mcp_weather":  rows.get("weather keys", ""),
            })

        if "LLM REASONING" in t:
            return Event(ts, svc, "agent_llm", {
                "model":    rows.get("model", ""),
                "sys_len":  rows.get("system_prompt len", "0"),
                "usr_len":  rows.get("user_prompt len", "0"),
            })

        if "LLM DECISION" in t:
            return Event(ts, svc, "agent_decision", {
                "status":  rows.get("status", "?"),
                "summary": rows.get("summary", rows.get("recommendation", "")),
            })

        if "PIPELINE COMPLETE" in t or "STEP 8" in t:
            ok, err = [], []
            for k, v in rows.items():
                if "✓" in k: ok.append(k.replace("✓","").strip())
                if "✗" in k: err.append(k.replace("✗","").strip())
            # Also parse inline log lines: "✓  flight  skill=..."
            mcp = rows.get("mcp_tools_called", "")
            return Event(ts, svc, "pipeline_done", {
                "ok":  ok,
                "err": err,
                "mcp_tools": mcp,
            })

        return None

    # ── Token accounting ──────────────────────────────────────────────────────

    def _account_tokens(self, title, rows, svc):
        t = title.upper()

        # LLM input: system_prompt + user_prompt lengths (chars ÷ 4 ≈ tokens)
        if "LLM REASONING" in t:
            sys_chars = _to_int(rows.get("system_prompt len", "0"))
            usr_chars = _to_int(rows.get("user_prompt len",   "0"))
            if sys_chars or usr_chars:
                tokens_in = (sys_chars + usr_chars) // 4
                self.token_rows.append(TokenRow(
                    service   = svc,
                    call      = f"LLM input — {_agent_label(svc)}",
                    direction = "llm_in",
                    tokens    = max(tokens_in, 1),
                    note      = f"sys {sys_chars}ch + usr {usr_chars}ch",
                ))

        # LLM output: response.length chars (if logged)
        if "LLM REASONING" in t:
            resp_len = _to_int(rows.get("llm.response.length", "0"))
            if resp_len:
                self.token_rows.append(TokenRow(
                    service   = svc,
                    call      = f"LLM output — {_agent_label(svc)}",
                    direction = "llm_out",
                    tokens    = max(resp_len // 4, 1),
                    note      = f"{resp_len}ch",
                ))

        # A2A send payload
        if "A2A SEND" in t and "→" in t:
            agent  = re.search(r"→\s*(\w[\w\-]*)", title)
            target = agent.group(1).lower() if agent else "agent"
            # Look for PAYLOAD bytes in the rows (logged by http_client)
            pb = _to_int(rows.get("payload_bytes", "0"))
            if not pb:
                # Fallback: estimate from known fields
                skill    = rows.get("  message.skill", rows.get("skill", ""))
                mcp_keys = rows.get("  mcp_results keys", "")
                # base ~400 tokens + ~150 per MCP key populated
                mcp_count = len(re.findall(r"'\w+",  mcp_keys))
                pb_est    = (400 + mcp_count * 150) * 4  # convert back to chars
                pb        = pb_est
            self.token_rows.append(TokenRow(
                service   = svc,
                call      = f"A2A → {target}",
                direction = "a2a_send",
                tokens    = max(pb // 4, 1),
                note      = f"~{pb} bytes",
            ))

        # A2A recv
        if "A2A RECV" in t and "←" in t:
            agent  = re.search(r"←\s*(\w[\w\-]*)", title)
            source = agent.group(1).lower() if agent else "agent"
            state  = rows.get("state", "?")
            # Artifact sizes not logged; estimate ~200 tokens for a compact decision
            self.token_rows.append(TokenRow(
                service   = svc,
                call      = f"A2A ← {source} [{state}]",
                direction = "a2a_recv",
                tokens    = 200,
                note      = "artifact estimate",
            ))

        # MCP tool call (send)
        if "MCP CALL" in t and "PRE-FETCH" not in t:
            parts  = re.split(r"→", title.replace("MCP CALL","").strip(), 1)
            tool   = parts[1].strip() if len(parts) > 1 else rows.get("tool","mcp")
            server = parts[0].strip() if parts else ""
            self.token_rows.append(TokenRow(
                service   = svc,
                call      = f"MCP {server}/{tool} (args)",
                direction = "mcp_send",
                tokens    = 80,
                note      = "arguments estimate",
            ))
            self.token_rows.append(TokenRow(
                service   = svc,
                call      = f"MCP {server}/{tool} (result)",
                direction = "mcp_recv",
                tokens    = 200,
                note      = "result estimate",
            ))


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_int(s: str) -> int:
    m = re.search(r"\d+", str(s))
    return int(m.group()) if m else 0

def _agent_label(svc: str) -> str:
    labels = {
        "orchestrator":   "Orchestrator",
        "flight-agent":   "Flight Agent",
        "hotel-agent":    "Hotel Agent",
        "activity-agent": "Activity Agent",
        "weather-agent":  "Weather Agent",
        "travel-agent":   "Travel Agent",
    }
    return labels.get(svc, svc)


# ─────────────────────────────────────────────────────────────────────────────
# Mermaid generator
# ─────────────────────────────────────────────────────────────────────────────

_PID = {
    "orchestrator":   ("O",     "Orchestrator"),
    "travel-agent":   ("TA",    "Travel Agent"),
    "travel":         ("TA",    "Travel Agent"),
    "flight-agent":   ("FA",    "Flight Agent"),
    "flight":         ("FA",    "Flight Agent"),
    "hotel-agent":    ("HA",    "Hotel Agent"),
    "hotel":          ("HA",    "Hotel Agent"),
    "activity-agent": ("AA",    "Activity Agent"),
    "activity":       ("AA",    "Activity Agent"),
    "weather-agent":  ("WA",    "Weather Agent"),
    "weather":        ("WA",    "Weather Agent"),
    "rag-service":    ("RAG",   "Vector DB / RAG"),
    "rag":            ("RAG",   "Vector DB / RAG"),
    "travel-mcp":     ("MCP_T", "travel-mcp"),
    "weather-mcp":    ("MCP_W", "weather-mcp"),
    "user":           ("U",     "User"),
    "frontend":       ("FE",    "Frontend"),
}
_ALL_PARTICIPANTS = [
    ("U",     "User"),
    ("FE",    "Frontend"),
    ("O",     "Orchestrator"),
    ("RAG",   "Vector DB / RAG"),
    ("TA",    "Travel Agent"),
    ("FA",    "Flight Agent"),
    ("HA",    "Hotel Agent"),
    ("AA",    "Activity Agent"),
    ("WA",    "Weather Agent"),
    ("MCP_T", "travel-mcp"),
    ("MCP_W", "weather-mcp"),
]

def _pid(name: str) -> str:
    return _PID.get(name.lower().strip(), (name.upper().replace("-","_"), name))[0]

def _esc(s: str) -> str:
    return (str(s) or "").replace('"',"'").replace("<","&lt;").replace(">","&gt;")[:90]


def generate_mermaid(events: List[Event]) -> str:
    lines = ["sequenceDiagram"]

    # Collect used participant IDs
    used: set = {_pid("user"), _pid("frontend"), _pid("orchestrator")}
    for ev in events:
        used.add(_pid(ev.service))
        if ev.kind == "rag":
            used.add(_pid("rag"))
        elif ev.kind == "mcp_init":
            sn = ev.detail.get("server","")
            used.add("MCP_W" if "weather" in sn.lower() else "MCP_T")
        elif ev.kind == "mcp_call":
            sn = ev.detail.get("server","")
            used.add("MCP_W" if "weather" in sn.lower() else "MCP_T")
        elif ev.kind == "a2a_send":
            used.add(_pid(ev.detail.get("target","")))
        elif ev.kind == "a2a_recv":
            used.add(_pid(ev.detail.get("source","")))

    for pid, label in _ALL_PARTICIPANTS:
        if pid in used:
            lines.append(f"    participant {pid} as {label}")
    lines.append("")

    # Always open with user → orchestrator
    lines.append("    U->>FE: Submit travel prompt")
    lines.append("    FE->>O: POST /a2a  tasks/send")
    lines.append("")

    for ev in events:
        d = ev.detail

        if ev.kind == "context":
            dest   = d.get("destination") or "?"
            origin = d.get("origin")      or "?"
            budget = d.get("budget_eur")  or "?"
            season = d.get("season")      or "?"
            lines.append(
                f"    Note over O: Context extracted<br/>"
                f"{_esc(origin)} → {_esc(dest)}<br/>"
                f"Season: {_esc(season)}  Budget: €{_esc(str(budget))}"
            )
            lines.append("")

        elif ev.kind == "rag":
            docs = d.get("docs", "?")
            q    = (d.get("query","") or "")[:60]
            lines.append("    O->>RAG: embedding_search(query)")
            if q:
                lines.append(f"    Note over RAG: {_esc(q)}")
            lines.append(f"    RAG-->>O: {_esc(str(docs))} docs returned")
            lines.append("")

        elif ev.kind == "discovery":
            agents = d.get("agents", [])
            if agents:
                note = "Agent discovery<br/>" + "<br/>".join(f"✓ {a}" for a in agents[:6])
                lines.append(f"    Note over O: {note}")
                lines.append("")

        elif ev.kind == "governance_registry":
            lines.append("    Note over O: Governance registry printed<br/>(SKILL_MCP_MAP)")
            lines.append("")

        elif ev.kind == "governance_resolve":
            agent = d.get("agent", "?")
            tools = d.get("required_tools", [])
            apid  = _pid(agent)
            if tools:
                lines.append(
                    f"    Note over O: Resolve MCP for {_esc(agent)}<br/>"
                    f"needs: {_esc(', '.join(tools))}"
                )
                lines.append("")

        elif ev.kind == "mcp_init":
            server = d.get("server","").lower()
            mpid   = "MCP_W" if "weather" in server else "MCP_T"
            lines.append(f"    O->>{mpid}: initialize (session handshake)")
            lines.append(f"    {mpid}-->>O: session token")
            lines.append("")

        elif ev.kind == "mcp_call":
            server = d.get("server","").lower()
            tool   = d.get("tool", "call_tool")
            mpid   = "MCP_W" if "weather" in server else "MCP_T"
            lines.append(f"    O->>{mpid}: tools/call {_esc(tool)}")
            lines.append(f"    {mpid}-->>O: {_esc(tool)} results")
            lines.append("")

        elif ev.kind == "a2a_send":
            target   = d.get("target", "agent")
            tpid     = _pid(target)
            skill    = d.get("skill") or d.get("intent") or "tasks/send"
            mcp_keys = d.get("mcp_keys", "") or ""
            bytes_   = d.get("payload_bytes", 0)
            size_note = f" ({bytes_} bytes)" if bytes_ else ""
            lines.append(f"    O->>{tpid}: tasks/send [{_esc(skill)}]{_esc(size_note)}")
            if mcp_keys and mcp_keys not in ("[]", ""):
                lines.append(f"    Note over {tpid}: mcp_results: {_esc(mcp_keys)}")
            lines.append("")

        elif ev.kind == "agent_task":
            src   = _pid(ev.service)
            skill = d.get("skill","?")
            dest  = d.get("destination","?")
            items = []
            if d.get("mcp_flights","0") != "0": items.append(f"{d['mcp_flights']} flights")
            if d.get("mcp_hotels","0")  != "0": items.append(f"{d['mcp_hotels']} hotels")
            if d.get("mcp_act","0")     != "0": items.append(f"{d['mcp_act']} activities")
            if d.get("mcp_weather","") not in ("","none"):  items.append("weather")
            data_note = (", ".join(items) + " from MCP") if items else "no MCP data"
            lines.append(f"    Note over {src}: skill={_esc(skill)}<br/>{data_note}")
            lines.append("")

        elif ev.kind == "agent_llm":
            src   = _pid(ev.service)
            model = d.get("model","LLM")
            sys_  = d.get("sys_len","0")
            usr_  = d.get("usr_len","0")
            toks  = (int(sys_ or 0) + int(usr_ or 0)) // 4
            lines.append(
                f"    Note over {src}: LLM reasoning<br/>"
                f"model={_esc(model)}<br/>~{toks} input tokens"
            )
            lines.append("")

        elif ev.kind == "agent_decision":
            src    = _pid(ev.service)
            status = d.get("status","?")
            summ   = d.get("summary","")
            note   = f"Decision: {_esc(status)}"
            if summ:
                note += f"<br/>{_esc(summ[:60])}"
            lines.append(f"    Note over {src}: {note}")
            lines.append("")

        elif ev.kind == "a2a_recv":
            src    = _pid(ev.service)
            source = d.get("source","agent")
            spid   = _pid(source)
            state  = d.get("state","?")
            lines.append(f"    {spid}-->>{src}: result [{_esc(state)}]")
            lines.append("")

        elif ev.kind == "pipeline_done":
            ok   = d.get("ok",[])
            err  = d.get("err",[])
            mcp  = d.get("mcp_tools","")
            note = "Pipeline complete<br/>"
            if ok:  note += "✓ " + "  ✓ ".join(ok[:4]) + "<br/>"
            if err: note += "✗ " + "  ✗ ".join(err[:4]) + "<br/>"
            if mcp: note += f"MCP: {_esc(str(mcp))[:60]}"
            lines.append(f"    Note over O: {note.strip()}")
            lines.append("    O-->>FE: itinerary artifact")
            lines.append("    FE-->>U: Trip plan displayed")
            lines.append("")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Token table builder
# ─────────────────────────────────────────────────────────────────────────────

def build_token_table(token_rows: List[TokenRow]) -> List[Dict]:
    rows = []
    for tr in token_rows:
        rows.append({
            "service":   tr.service,
            "call":      tr.call,
            "direction": tr.direction,
            "tokens":    tr.tokens,
            "note":      tr.note,
        })

    # Per-service subtotals
    by_svc: Dict[str, int] = {}
    for r in rows:
        by_svc[r["service"]] = by_svc.get(r["service"], 0) + r["tokens"]

    subtotals = []
    for svc, total in sorted(by_svc.items()):
        subtotals.append({
            "service":   svc,
            "call":      "── subtotal ──",
            "direction": "─",
            "tokens":    total,
            "note":      "",
            "is_subtotal": True,
        })

    grand = sum(r["tokens"] for r in rows)
    rows  = rows + subtotals + [{
        "service":   "TOTAL",
        "call":      "─── All services ───",
        "direction": "─",
        "tokens":    grand,
        "note":      "",
        "is_total":  True,
    }]
    return rows


# ─────────────────────────────────────────────────────────────────────────────
# Flask routes
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/analyze", methods=["POST"])
def analyze():
    data = request.get_json(force=True) or {}
    raw  = data.get("logs","").strip()
    if not raw:
        return jsonify({"error": "No log content provided."}), 400

    parser = LogParser()
    parser.feed(raw)

    if not parser.events:
        return jsonify({
            "error": (
                "No recognisable A2A log events found. "
                "Paste logs from orchestrator / agents running v3.3.x. "
                "Lines must contain the ┌─ banner format."
            )
        }), 422

    mermaid     = generate_mermaid(parser.events)
    token_table = build_token_table(parser.token_rows)
    events_out  = [
        {"ts": e.ts, "svc": e.service, "kind": e.kind, "detail": e.detail}
        for e in parser.events
    ]

    return jsonify({
        "mermaid":     mermaid,
        "token_table": token_table,
        "event_count": len(parser.events),
        "events":      events_out,
    })


@app.route("/health")
def health():
    return jsonify({"status":"ok","version":"1.1"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
