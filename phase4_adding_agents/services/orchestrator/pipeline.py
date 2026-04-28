"""
orchestrator/pipeline.py
─────────────────────────
Core trip-planning pipeline.  Orchestrates all the specialist modules:
  context_extractor → rag_client → agent_discovery
  → mcp_gateway (governance) → a2a_client (fan-out) → summary

v4.0 additions:
  • Skill recipe narration via log_utils.narrate_skill() before every A2A dispatch
  • Agentic budget loop (max MAX_BUDGET_ITERATIONS) for flight + hotel:
      - After each rank round, sum best_flight_price + best_hotel_total_price
      - If over budget → call mcp_gateway.refetch_flights_hotels() with an
        alternate date (±week, season-guarded) and re-rank
      - Track the closest-to-budget combination across all iterations
      - On iteration MAX_BUDGET_ITERATIONS failure → emit WARNING log and
        return the closest combo with an over_budget_warning field
  • Activity + weather agents run once, after the budget loop completes
    (they are not budget-sensitive)
"""

import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import log_utils as L
import shared.tracing as tracing
from config import (
    LLM_MODEL, MAX_BUDGET_ITERATIONS, SEASON_BOUNDS, SEASON_DATE,
    SKILL_MCP_MAP, TRAVEL_MCP_URL, WEATHER_MCP_URL,
)
from context_extractor import extract_user_context
from rag_client import call_rag, build_rag_context
from agent_discovery import discover_agents
from mcp_gateway import (
    resolve_mcp_for_agent, clear_session_cache, refetch_flights_hotels,
)
from a2a_client import a2a_send_task
from shared.a2a_protocol import A2AMessage, get_artifact_data
from guardrail import (
    scan_user_prompt, scan_rag_context, scan_mcp_data, scan_llm_response,
    guardrail_blocked_message, F5GUARDRAILS_ENABLED,
)

logger = logging.getLogger("orchestrator")


# ═════════════════════════════════════════════════════════════════════════════
# Public entry point
# ═════════════════════════════════════════════════════════════════════════════

def run_planning_pipeline(
    prompt:          str,
    conversation_id: str,
    session_id:      str,
) -> Dict[str, Any]:
    """
    Full trip-planning pipeline.  Returns the itinerary dict that becomes
    the 'itinerary' artifact in the A2A task response.
    """
    tracer = tracing.get_tracer("orchestrator")

    with tracer.start_as_current_span("plan_trip_pipeline"):

        # Clear MCP session tokens — fresh handshake per request
        clear_session_cache()

        # ── Guardrail SCAN POINT 1 · user prompt ─────────────────────────────
        g_prompt = scan_user_prompt(prompt)
        if not g_prompt["allowed"]:
            logger.warning("[GUARDRAIL] User prompt FLAGGED — aborting pipeline")
            return _guardrail_abort(conversation_id, "user prompt",
                                    g_prompt["blocked_reason"])

        # ── Step 1 · Context extraction ───────────────────────────────────────
        user_context = extract_user_context(prompt)
        dest     = user_context.get("destination") or "Unknown"
        origin   = user_context.get("origin")      or "Unknown"
        season   = user_context.get("season")
        duration = user_context.get("duration")
        budget   = user_context.get("budget_eur")

        # ── Step 2 · RAG ──────────────────────────────────────────────────────
        prompt_tokens = L.count_tokens(prompt)
        rag_docs      = call_rag(prompt)
        rag_context   = build_rag_context(rag_docs)
        rag_tokens    = L.count_tokens(rag_context)

        # ── Guardrail SCAN POINT 2 · RAG retrieved context ───────────────────
        g_rag = scan_rag_context(rag_context)
        if not g_rag["allowed"]:
            logger.warning("[GUARDRAIL] RAG context FLAGGED — aborting pipeline")
            return _guardrail_abort(conversation_id, "RAG context",
                                    g_rag["blocked_reason"])

        L.log_token_accounting(
            label         = "after RAG  (prompt + RAG context)",
            prompt_tokens = prompt_tokens,
            rag_tokens    = rag_tokens,
            total_tokens  = prompt_tokens + rag_tokens,
        )

        dest_lower = dest.lower()
        if dest_lower and rag_docs:
            if all(dest_lower not in d.get("content", "").lower() for d in rag_docs):
                logger.warning("⚠ RAG docs don't mention '%s' — may be noise", dest)

        # ── Step 3 · Agent discovery ──────────────────────────────────────────
        agents = discover_agents()

        # ── Step 4 · Print governance registry ───────────────────────────────
        _log_governance_registry()

        # ── Step 5 · MCP pre-fetch (governance-driven) ────────────────────────
        L.banner("STEP 5 · DYNAMIC MCP PRE-FETCH  (governance-driven)")
        L.row("pattern",
              "orchestrator reads agent cards → resolves SKILL_MCP_MAP → calls MCP")
        L.banner_end()

        mcp_cache:         Dict[str, Any] = {}   # shared + mutated across iterations
        agent_mcp_results: Dict[str, Dict] = {}

        for name, info in agents.items():
            if info.get("error"):
                continue
            mcp_results = resolve_mcp_for_agent(
                agent_name   = name,
                card         = info.get("card", {}),
                user_context = user_context,
                session_id   = session_id,
                mcp_cache    = mcp_cache,
            )
            agent_mcp_results[name] = mcp_results

        _log_mcp_cache_summary(mcp_cache)

        # ── Guardrail SCAN POINT 3 · MCP tool results ───────────────────────
        g_mcp = scan_mcp_data(mcp_cache)
        if not g_mcp["allowed"]:
            logger.warning("[GUARDRAIL] MCP data FLAGGED — aborting pipeline")
            return _guardrail_abort(conversation_id, "MCP data",
                                    g_mcp["blocked_reason"])

        # ── Step 6 · Intent summary ───────────────────────────────────────────
        orchestrator_intent = {
            "origin":      origin,
            "destination": dest,
            "season":      season,
            "budget_eur":  budget,
            "duration":    duration,
            "interests":   user_context.get("interests"),
        }
        L.banner("STEP 6 · ORCHESTRATOR INTENT SUMMARY")
        for k, v in orchestrator_intent.items():
            L.row(k, v)
        L.banner_end()

        # ── Step 7 · A2A fan-out with budget loop ─────────────────────────────
        L.box("STEP 7  —  AGENTIC BUDGET LOOP  (flight + hotel)")

        loop_log:           List[Dict[str, Any]] = []
        specialist_results: Dict[str, Any]       = {}
        over_budget_warning: Optional[str]       = None

        # Derive baseline departure date from season
        base_date   = SEASON_DATE.get(season or "", "2025-07-15")
        current_date = base_date

        # Tracks the closest combo across all iterations
        best_combo: Optional[Dict[str, Any]] = None
        best_gap:   float = float("inf")

        for iteration in range(1, MAX_BUDGET_ITERATIONS + 1):

            # ── iteration header ──────────────────────────────────────────────
            L.box(
                f"BUDGET LOOP  iteration {iteration}/{MAX_BUDGET_ITERATIONS}  "
                f"date={current_date}  budget=€{budget}"
            )

            # ── on iterations > 1, request alternate dates from MCP ───────────
            if iteration > 1:
                L.banner(
                    f"BUDGET LOOP · ALTERNATE DATE REQUEST  iteration={iteration}"
                )
                L.row("reason",        "previous flight+hotel total exceeded budget")
                L.row("previous_date", current_date)
                current_date = _alternate_date(base_date, iteration, season)
                L.row("new_date",      current_date)
                L.row("season_guard",  season or "none")
                L.row("strategy",
                      f"shift {'forward' if iteration % 2 == 0 else 'backward'} "
                      f"by {7 * iteration} days, clamp to season bounds")
                L.banner_end()

                # Re-fetch flights + hotels for the new date
                flight_mcp, hotel_mcp = refetch_flights_hotels(
                    user_context = user_context,
                    new_date     = current_date,
                    session_id   = session_id,
                    mcp_cache    = mcp_cache,
                )
                agent_mcp_results["flight"]  = flight_mcp
                agent_mcp_results["hotel"]   = hotel_mcp

            # ── dispatch flight agent ─────────────────────────────────────────
            flight_mcp_tokens = L.count_tokens(
                json.dumps(agent_mcp_results.get("flight", {})))
            L.log_token_accounting(
                label         = f"flight-agent dispatch  iter={iteration}",
                prompt_tokens = prompt_tokens,
                rag_tokens    = rag_tokens,
                mcp_tokens    = flight_mcp_tokens,
                total_tokens  = prompt_tokens + rag_tokens + flight_mcp_tokens,
            )
            L.narrate_skill("rank_flights", "flight-agent")
            _dispatch_agent(
                name             = "flight",
                skill            = "rank_flights",
                intent           = "rank_flights",
                agents           = agents,
                session_id       = session_id,
                conversation_id  = f"{conversation_id}-iter{iteration}",
                user_context     = user_context,
                rag_context      = rag_context,
                mcp_results      = agent_mcp_results.get("flight", {}),
                extra_payload    = {
                    "prompt":      prompt,
                    "budget_eur":  budget,
                    "origin":      origin,
                    "destination": dest,
                    "season":      season,
                    "date":        current_date,
                },
                loop_log           = loop_log,
                specialist_results = specialist_results,
                result_key         = f"flight_iter{iteration}",
            )

            # ── dispatch hotel agent ──────────────────────────────────────────
            hotel_mcp_tokens = L.count_tokens(
                json.dumps(agent_mcp_results.get("hotel", {})))
            L.log_token_accounting(
                label         = f"hotel-agent dispatch  iter={iteration}",
                prompt_tokens = prompt_tokens,
                rag_tokens    = rag_tokens,
                mcp_tokens    = hotel_mcp_tokens,
                total_tokens  = prompt_tokens + rag_tokens + hotel_mcp_tokens,
            )
            L.narrate_skill("rank_hotels", "hotel-agent")
            _dispatch_agent(
                name             = "hotel",
                skill            = "rank_hotels",
                intent           = "rank_hotels",
                agents           = agents,
                session_id       = session_id,
                conversation_id  = f"{conversation_id}-iter{iteration}",
                user_context     = user_context,
                rag_context      = rag_context,
                mcp_results      = agent_mcp_results.get("hotel", {}),
                extra_payload    = {
                    "prompt":      prompt,
                    "budget_eur":  budget,
                    "destination": dest,
                    "duration":    duration,
                    "date":        current_date,
                },
                loop_log           = loop_log,
                specialist_results = specialist_results,
                result_key         = f"hotel_iter{iteration}",
            )

            # ── budget check ──────────────────────────────────────────────────
            flight_cost = _extract_best_flight_cost(
                specialist_results.get(f"flight_iter{iteration}"))
            hotel_cost  = _extract_best_hotel_total(
                specialist_results.get(f"hotel_iter{iteration}"))

            L.banner(f"BUDGET CHECK  iteration={iteration}")
            L.row("budget_eur",         f"€{budget}" if budget else "unset")
            L.row("best_flight_cost",   f"€{flight_cost:.0f}" if flight_cost is not None else "N/A  (agent error — using fallback sort)")
            L.row("best_hotel_total_7n",f"€{hotel_cost:.0f}"  if hotel_cost  is not None else "N/A  (agent error — using fallback sort)")

            within_budget = False

            if flight_cost is not None and hotel_cost is not None and budget:
                total = flight_cost + hotel_cost
                gap   = total - budget
                L.row("combined_total",  f"€{total:.0f}")
                L.row("gap_vs_budget",   f"€{gap:+.0f}")

                # track closest combo regardless of outcome
                if abs(gap) < abs(best_gap):
                    best_gap   = gap
                    best_combo = {
                        "iteration":    iteration,
                        "date":         current_date,
                        "flight_cost":  flight_cost,
                        "hotel_cost":   hotel_cost,
                        "total":        total,
                        "gap":          gap,
                        "flight_data":  _pick_best_flight(
                            specialist_results.get(f"flight_iter{iteration}")),
                        "hotel_data":   _pick_best_hotel(
                            specialist_results.get(f"hotel_iter{iteration}")),
                    }

                if total <= budget:
                    L.row("verdict", "✓  WITHIN BUDGET — stopping budget loop")
                    L.banner_end()
                    within_budget = True
                    # promote this iteration's results to canonical keys
                    specialist_results["flight"] = specialist_results[f"flight_iter{iteration}"]
                    specialist_results["hotel"]  = specialist_results[f"hotel_iter{iteration}"]
                    break
                else:
                    if iteration < MAX_BUDGET_ITERATIONS:
                        L.row("verdict",
                              f"✗  OVER BUDGET by €{gap:.0f} — "
                              f"will request alternate dates (iteration {iteration+1})")
                    else:
                        L.row("verdict",
                              f"✗  OVER BUDGET by €{gap:.0f} — "
                              f"MAX ITERATIONS REACHED — returning closest combo")
                    L.banner_end()
            else:
                # budget unset or agent errors — treat as within budget and proceed
                L.row("verdict",
                      "?  budget check skipped "
                      "(budget unset or agent returned error) — proceeding")
                L.banner_end()
                within_budget = True
                specialist_results["flight"] = specialist_results.get(
                    f"flight_iter{iteration}")
                specialist_results["hotel"]  = specialist_results.get(
                    f"hotel_iter{iteration}")
                break

        # ── over-budget warning after exhausting all iterations ───────────────
        if not within_budget and best_combo and best_combo.get("gap", 0) > 0:
            over_budget_warning = (
                f"⚠️  Sorry, this is the closest combination I could find within "
                f"your tight budget of €{budget:.0f}. "
                f"After {MAX_BUDGET_ITERATIONS} attempts across different dates, "
                f"the best available option costs €{best_combo['total']:.0f} "
                f"(€{best_combo['gap']:.0f} over budget) "
                f"for departure around {best_combo['date']}."
            )
            logger.warning("│")
            logger.warning("│  ⚠️  BUDGET WARNING")
            logger.warning("│  %s", over_budget_warning)
            logger.warning("│")
            # promote the closest combo as the canonical result
            specialist_results["flight"] = specialist_results.get(
                f"flight_iter{best_combo['iteration']}")
            specialist_results["hotel"]  = specialist_results.get(
                f"hotel_iter{best_combo['iteration']}")

        # ── Step 7b · Activity + weather (once, after budget loop) ────────────
        L.box("STEP 7b  —  A2A FAN-OUT  activity + weather")

        activity_mcp_tokens = L.count_tokens(
            json.dumps(agent_mcp_results.get("activity", {})))
        L.log_token_accounting(
            label         = "activity-agent dispatch",
            prompt_tokens = prompt_tokens,
            rag_tokens    = rag_tokens,
            mcp_tokens    = activity_mcp_tokens,
            total_tokens  = prompt_tokens + rag_tokens + activity_mcp_tokens,
        )
        L.narrate_skill("suggest_activities", "activity-agent")
        _dispatch_agent(
            name             = "activity",
            skill            = "suggest_activities",
            intent           = "suggest_activities",
            agents           = agents,
            session_id       = session_id,
            conversation_id  = conversation_id,
            user_context     = user_context,
            rag_context      = rag_context,
            mcp_results      = agent_mcp_results.get("activity", {}),
            extra_payload    = {
                "prompt":      prompt,
                "destination": dest,
                "season":      season,
                "interests":   user_context.get("interests"),
            },
            loop_log           = loop_log,
            specialist_results = specialist_results,
        )

        weather_mcp_tokens = L.count_tokens(
            json.dumps(agent_mcp_results.get("weather", {})))
        L.log_token_accounting(
            label         = "weather-agent dispatch",
            prompt_tokens = prompt_tokens,
            rag_tokens    = rag_tokens,
            mcp_tokens    = weather_mcp_tokens,
            total_tokens  = prompt_tokens + rag_tokens + weather_mcp_tokens,
        )
        L.narrate_skill("get_forecast", "weather-agent")
        _dispatch_agent(
            name             = "weather",
            skill            = "get_forecast",
            intent           = "get_forecast",
            agents           = agents,
            session_id       = session_id,
            conversation_id  = conversation_id,
            user_context     = user_context,
            rag_context      = rag_context,
            mcp_results      = agent_mcp_results.get("weather", {}),
            extra_payload    = {
                "prompt":      prompt,
                "destination": dest,
                "season":      season,
            },
            loop_log           = loop_log,
            specialist_results = specialist_results,
        )

        # ── Step 8 · Pipeline summary ─────────────────────────────────────────
        L.box("STEP 8  —  PIPELINE COMPLETE")
        L.row("conversation_id",      conversation_id)
        L.row("rag_docs_used",        len(rag_docs))
        L.row("mcp_tools_called",     list(mcp_cache.keys()))
        L.row("budget_iterations",    f"{iteration}/{MAX_BUDGET_ITERATIONS}")
        if over_budget_warning:
            logger.warning("│  %s", over_budget_warning)
        logger.info("│")
        for entry in loop_log:
            icon = "✓" if entry.get("state") == "completed" else "✗"
            logger.info("│  %s  %-12s  skill=%-28s  mcp=%s  state=%s",
                        icon,
                        entry.get("agent", "?"),
                        entry.get("skill", "?"),
                        entry.get("mcp_tools_used", []),
                        entry.get("state", "?"))
        logger.info("│")

        # ── TOKEN ACCOUNTING · COMPLETE  (post-dispatch totals) ───────────────
        # Agentic mode: each agent receives prompt+RAG+its MCP slice independently.
        # Total tokens = sum across all agent dispatches.  We report the
        # orchestrator-side estimates; agents report their own Ollama counts in
        # their individual logs (eval_count / prompt_eval_count).
        _mcp_total_tokens = L.count_tokens(
            json.dumps(mcp_cache, ensure_ascii=False, separators=(",", ":")))
        _n_agents         = len([e for e in loop_log if e.get("state") == "completed"])
        _n_failed         = len([e for e in loop_log if e.get("state") != "completed"])
        # Conservative estimate: each agent sees prompt_tokens + rag_tokens + its MCP slice
        # We use mcp_total / n_agents as average MCP slice per agent
        _avg_mcp          = _mcp_total_tokens // max(len(loop_log), 1)
        _per_agent_input  = prompt_tokens + rag_tokens + _avg_mcp
        _total_est_input  = _per_agent_input * max(len(loop_log), 1)

        L.banner("TOKEN ACCOUNTING  ·  COMPLETE  (post-dispatch)")
        L.row("─── INPUT (orchestrator estimates) ─", "")
        L.row("  prompt_tokens",         f"{prompt_tokens:,}")
        L.row("  rag_context_tokens",    f"{rag_tokens:,}")
        L.row("  mcp_data_tokens_total", f"{_mcp_total_tokens:,}  (all tools combined)")
        L.row("  avg_mcp_per_agent",     f"{_avg_mcp:,}  tokens")
        L.row("  per_agent_input_est",   f"{_per_agent_input:,}  tokens  (prompt+rag+mcp_slice)")
        L.row("  agents_dispatched",     f"{len(loop_log)}")
        L.row("  total_input_est",
              f"{_total_est_input:,}  tokens  ({len(loop_log)} agents × {_per_agent_input:,})")
        L.row("─── AGENT STATUS ─────────────────────", "")
        L.row("  completed", f"{_n_agents}")
        L.row("  failed",    f"{_n_failed}")
        L.row("─── NOTE ─────────────────────────────", "")
        L.row("  authoritative counts",
              "see each agent's STEP 7 LLM RESPONSE banner (Ollama eval_count)")
        L.banner_end()

        # ── Guardrail SCAN POINT 4 · synthesised response ───────────────────
        _summary_text = _build_response_summary(specialist_results, orchestrator_intent)
        g_output = scan_llm_response(_summary_text)
        if not g_output["allowed"]:
            logger.warning("[GUARDRAIL] Agent response summary FLAGGED — blocking")
            return _guardrail_abort(conversation_id, "agent response",
                                    g_output["blocked_reason"],
                                    loop=loop_log, intent=orchestrator_intent)

        return {
            "conversation_id":    conversation_id,
            "guardrail_blocked":  False,
            "intent":             orchestrator_intent,
            "loop":               loop_log,
            "user_context":       user_context,
            "rag_docs_count":     len(rag_docs),
            "agents_discovered":  list(agents.keys()),
            "mcp_data":           mcp_cache,
            "agent_results":      specialist_results,
            "origin":             origin,
            "destination":        dest,
            "season":             season,
            "budget_eur":         budget,
            "duration":           duration,
            "best_combo":         best_combo,
            "over_budget_warning": over_budget_warning,
            "budget_iterations":  iteration,
        }


# ═════════════════════════════════════════════════════════════════════════════
# Budget loop helpers
# ═════════════════════════════════════════════════════════════════════════════

def _alternate_date(base_date: str, iteration: int, season: Optional[str]) -> str:
    """
    Return an alternate departure date for the given iteration, staying
    within the declared season.

    Strategy:
      iteration 2 → base + 7 days
      iteration 3 → base - 7 days
      iteration 4 → base + 14 days  (if MAX_BUDGET_ITERATIONS ever raised)

    The result is clamped so the month stays within SEASON_BOUNDS.
    """
    offsets = {2: 7, 3: -7, 4: 14, 5: -14}
    delta   = offsets.get(iteration, 7 * (iteration - 1))

    try:
        base = datetime.strptime(base_date, "%Y-%m-%d")
    except ValueError:
        base = datetime(2025, 4, 15)

    candidate = base + timedelta(days=delta)

    # season guard
    bounds = SEASON_BOUNDS.get((season or "").lower())
    if bounds:
        lo, hi = bounds
        if lo <= hi:
            # normal range e.g. spring (3–5)
            if not (lo <= candidate.month <= hi):
                # flip direction
                candidate = base - timedelta(days=delta)
                if not (lo <= candidate.month <= hi):
                    candidate = base  # fall back to base
        else:
            # winter wraps year boundary (12–2)
            if not (candidate.month >= lo or candidate.month <= hi):
                candidate = base - timedelta(days=delta)
                if not (candidate.month >= lo or candidate.month <= hi):
                    candidate = base

    return candidate.strftime("%Y-%m-%d")


def _extract_best_flight_cost(result: Optional[Any]) -> Optional[float]:
    """
    Extract the price of the best-ranked flight from a flight agent result.
    Returns None if the result is missing or malformed (agent error path).
    """
    if not result:
        return None
    try:
        # result is get_artifact_data() output — a plain dict
        decision = result.get("decision", {})
        ranked   = decision.get("ranked_flights", [])
        if not ranked:
            return None
        idx   = max(0, decision.get("best_choice", 1) - 1)
        best  = ranked[min(idx, len(ranked) - 1)]
        return float(best.get("price", 0))
    except Exception as exc:
        logger.debug("_extract_best_flight_cost failed: %s", exc)
        return None


def _extract_best_hotel_total(result: Optional[Any]) -> Optional[float]:
    """
    Extract the 7-night total of the top-ranked hotel from a hotel agent result.
    Returns None if the result is missing or malformed (agent error path).
    """
    if not result:
        return None
    try:
        ranked = result.get("ranked_hotels", [])
        if not ranked:
            return None
        best = ranked[0]
        # prefer explicit total_price, fall back to per_night × 7
        total = best.get("total_price") or (
            float(best.get("price_per_night_eur", 0)) * 7
        )
        return float(total)
    except Exception as exc:
        logger.debug("_extract_best_hotel_total failed: %s", exc)
        return None


def _pick_best_flight(result: Optional[Any]) -> Optional[Dict]:
    """Return the single best flight dict, or None."""
    if not result:
        return None
    try:
        decision = result.get("decision", {})
        ranked   = decision.get("ranked_flights", [])
        if not ranked:
            return None
        idx = max(0, decision.get("best_choice", 1) - 1)
        return ranked[min(idx, len(ranked) - 1)]
    except Exception:
        return None


def _pick_best_hotel(result: Optional[Any]) -> Optional[Dict]:
    """Return the top-ranked hotel dict, or None."""
    if not result:
        return None
    try:
        ranked = result.get("ranked_hotels", [])
        return ranked[0] if ranked else None
    except Exception:
        return None


# ═════════════════════════════════════════════════════════════════════════════
# Dispatch helper
# ═════════════════════════════════════════════════════════════════════════════

def _dispatch_agent(
    name:               str,
    skill:              str,
    intent:             str,
    agents:             Dict[str, Any],
    session_id:         str,
    conversation_id:    str,
    user_context:       Dict[str, Any],
    rag_context:        str,
    mcp_results:        Dict[str, Any],
    extra_payload:      Dict[str, Any],
    loop_log:           List[Dict[str, Any]],
    specialist_results: Dict[str, Any],
    result_key:         Optional[str] = None,
) -> None:
    """
    Build an A2AMessage and send it to the named agent via a2a_send_task().
    Stores the extracted artifact data in specialist_results under result_key
    (defaults to agent name).
    """
    if name not in agents or agents[name].get("error"):
        logger.warning("  %s skipped — not available", name)
        return

    store_key    = result_key or name
    mcp_keys_used = list(mcp_results.keys())

    msg = A2AMessage.user_data({
        "skill":        skill,
        "intent":       intent,
        "user_context": user_context,
        "rag_context":  rag_context,
        "payload":      extra_payload,
        "mcp_results":  mcp_results,
    })

    try:
        task = a2a_send_task(
            agent_name = name,
            agent_url  = agents[name]["url"],
            task_id    = f"{conversation_id}-{name}",
            session_id = session_id,
            message    = msg,
            metadata   = {
                "skill":          skill,
                "mcp_tools_used": mcp_keys_used,
                "mcp_governance": "orchestrator",
            },
        )
        result_artifact = get_artifact_data(task, f"{name}_result")
        specialist_results[store_key] = result_artifact or task
        loop_log.append({
            "agent":          name,
            "skill":          skill,
            "mcp_tools_used": mcp_keys_used,
            "state":          task.get("status", {}).get("state"),
            "task_id":        task.get("id"),
        })
    except Exception as exc:
        logger.error("%s AGENT FAILED: %s", name.upper(), exc)
        specialist_results[store_key] = None
        loop_log.append({
            "agent": name, "skill": skill,
            "state": "error", "error": str(exc),
            "mcp_tools_used": mcp_keys_used,
        })


# ═════════════════════════════════════════════════════════════════════════════
# Governance registry log
# ═════════════════════════════════════════════════════════════════════════════

def _log_governance_registry():
    """Print the SKILL_MCP_MAP in a readable table format."""
    L.banner("STEP 4 · SKILL → MCP GOVERNANCE REGISTRY  (keyed by tool name)")
    logger.info("│")
    logger.info("│  %-28s %-20s %-12s",
                "TOOL NAME (requires_mcp_tools)", "MCP SERVER", "RESULT KEY")
    logger.info("│  %s", "─" * 65)
    for tool_name, reg in SKILL_MCP_MAP.items():
        logger.info("│  %-28s %-20s %-12s",
                    tool_name, reg["mcp_server"], reg["result_key"])
    logger.info("│")
    L.row("TRAVEL_MCP_URL",  TRAVEL_MCP_URL)
    L.row("WEATHER_MCP_URL", WEATHER_MCP_URL)
    L.row("Registry keyed by", "MCP TOOL NAME — matches requires_mcp_tools in agent cards")
    L.row("Agents own MCP?",  "NO — orchestrator is the sole MCP gateway")
    L.banner_end()


def _log_mcp_cache_summary(mcp_cache: Dict[str, Any]) -> None:
    L.banner("STEP 5b · MCP CACHE SUMMARY")
    if mcp_cache:
        for key, val in mcp_cache.items():
            L.row(f"  cached [{key}]", val, 400)
    else:
        L.row("  (empty — no MCP tools were resolved)")
    L.banner_end()


def _build_response_summary(specialist_results: Dict[str, Any],
                             intent: Dict[str, Any]) -> str:
    """
    Build a compact plaintext summary of the agent results to send to the
    CalypsoAI output scanner.  We don't send the full JSON (too large) —
    just enough for the scanner to detect PII, data leakage, policy violations.
    """
    parts = [
        f"Trip plan for {intent.get('destination','?')} "
        f"({intent.get('season','?')}, {intent.get('duration','?')})",
    ]
    # Flight
    flight = specialist_results.get("flight") or {}
    decision = flight.get("decision", {})
    ranked = decision.get("ranked_flights", [])
    if ranked:
        best = ranked[0]
        parts.append(
            f"Flight: {best.get('airline','?')} "
            f"€{best.get('price','?')} "
            f"{best.get('departure_time','?')}→{best.get('arrival_time','?')}"
        )
    # Hotel
    hotel = specialist_results.get("hotel") or {}
    ranked_h = hotel.get("ranked_hotels", [])
    if ranked_h:
        best_h = ranked_h[0]
        parts.append(
            f"Hotel: {best_h.get('hotel_name','?')} "
            f"★{best_h.get('stars','?')} "
            f"€{best_h.get('price_per_night_eur','?')}/night"
        )
    # Activities
    activity = specialist_results.get("activity") or {}
    acts = (activity.get("decision") or {}).get("selected_activities", [])
    if acts:
        parts.append("Activities: " + ", ".join(a.get("title","?") for a in acts[:5]))
    # Weather
    weather = specialist_results.get("weather") or {}
    if weather.get("summary"):
        parts.append(f"Weather: {weather['summary']}")

    return "\n".join(parts)


def _guardrail_abort(conversation_id: str, scan_point: str,
                     blocked_reason: str,
                     loop: list = None,
                     intent: Dict[str, Any] = None) -> Dict[str, Any]:
    """Standard early-return dict when any guardrail scan flags content."""
    return {
        "conversation_id":    conversation_id,
        "guardrail_blocked":  True,
        "guardrail_outcome":  "flagged",
        "guardrail_reason":   blocked_reason,
        "guardrail_stage":    scan_point,
        "response":           guardrail_blocked_message(blocked_reason, scan_point),
        "intent":             intent or {},
        "loop":               loop or [],
        "agent_results":      {},
        "mcp_data":           {},
    }
