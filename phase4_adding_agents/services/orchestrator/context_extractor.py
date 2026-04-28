"""
orchestrator/context_extractor.py
──────────────────────────────────
Extracts structured travel intent from a free-text user prompt.
No external calls — pure regex / keyword matching.
"""

import re
from typing import Dict, List, Optional, Any

import log_utils as L

# ── City aliases ──────────────────────────────────────────────────────────────

CITY_ALIASES: Dict[str, str] = {
    "rome": "Rome", "roma": "Rome",
    "barcelona": "Barcelona", "madrid": "Madrid",
    "seville": "Seville", "sevilla": "Seville",
    "paris": "Paris", "lyon": "Lyon", "nice": "Nice", "marseille": "Marseille",
    "london": "London", "edinburgh": "Edinburgh",
    "berlin": "Berlin", "munich": "Munich", "münchen": "Munich",
    "amsterdam": "Amsterdam",
    "lisbon": "Lisbon", "lisboa": "Lisbon", "porto": "Porto",
    "athens": "Athens", "santorini": "Santorini",
    "dubai": "Dubai", "istanbul": "Istanbul", "prague": "Prague",
    "budapest": "Budapest", "vienna": "Vienna", "wien": "Vienna",
    "tokyo": "Tokyo", "bangkok": "Bangkok", "singapore": "Singapore",
    "new york": "New York", "los angeles": "Los Angeles",
    "milan": "Milan", "milano": "Milan", "florence": "Florence",
    "firenze": "Florence", "venice": "Venice", "venezia": "Venice",
    "naples": "Naples", "napoli": "Naples",
}

# ── Season keywords ───────────────────────────────────────────────────────────

SEASON_KEYWORDS: Dict[str, str] = {
    "spring": "spring", "summer": "summer",
    "autumn": "autumn", "fall": "autumn", "winter": "winter",
    "january": "winter", "february": "winter",
    "march": "spring", "april": "spring", "may": "spring",
    "june": "summer", "july": "summer", "august": "summer",
    "september": "autumn", "october": "autumn", "november": "autumn",
    "december": "winter",
}

# ── Interest keywords ─────────────────────────────────────────────────────────

INTEREST_KEYWORDS: Dict[str, str] = {
    "museum": "museums", "museums": "museums",
    "shop": "shopping", "shopping": "shopping",
    "hiking": "hiking", "hike": "hiking",
    "beach": "beach", "beaches": "beach",
    "bike": "biking", "biking": "biking", "cycling": "biking",
    "nature": "nature", "art": "art", "gallery": "art",
    "food": "food", "cuisine": "food", "restaurant": "food",
    "nightlife": "nightlife", "history": "history", "historical": "history",
    "architecture": "architecture", "wine": "wine",
    "family": "family", "restful": "relaxation", "relax": "relaxation",
    "spa": "spa", "wellness": "wellness",
}

# ── Patterns ──────────────────────────────────────────────────────────────────

_BUDGET_RE = re.compile(
    r"(?:budget[^\d€$£]*|€|£|\$)?(\d[\d,\.]{1,8})\s*(?:euro|eur|€|gbp|usd|\$)?",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(
    r"(\d+|one|two|three|four|five|six|seven|a)\s*(week|day|night)",
    re.IGNORECASE,
)
_NUM_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4,
              "five": 5, "six": 6, "seven": 7, "a": 1}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _find_cities(text_lower: str) -> List[str]:
    found: Dict[str, str] = {}
    for alias in sorted(CITY_ALIASES, key=len, reverse=True):
        if alias in text_lower and CITY_ALIASES[alias] not in found.values():
            found[alias] = CITY_ALIASES[alias]
    return list(dict.fromkeys(found.values()))


# ── Public API ────────────────────────────────────────────────────────────────

def extract_user_context(prompt: str) -> Dict[str, Any]:
    """
    Parse a free-text travel prompt into a structured context dict.
    Logs the extraction step with the ┌─ banner format.
    """
    p      = prompt.lower()
    cities = _find_cities(p)

    origin: Optional[str]      = None
    destination: Optional[str] = None

    from_m = re.search(r"\bfrom\s+([a-z\s]+?)(?:\s+to|\s+in|\s+for|\s+with|,|$)", p)
    to_m   = re.search(r"\bto\s+([a-z\s]+?)(?:\s+in|\s+for|\s+with|,|$)", p)

    if from_m:
        frag = from_m.group(1).strip()
        for alias, canon in CITY_ALIASES.items():
            if alias in frag:
                origin = canon
                break
    if to_m:
        frag = to_m.group(1).strip()
        for alias, canon in CITY_ALIASES.items():
            if alias in frag:
                destination = canon
                break

    if not origin and not destination and len(cities) >= 2:
        origin, destination = cities[0], cities[1]
    elif not destination and cities:
        destination = cities[0] if not origin else (
            cities[1] if len(cities) > 1 else cities[0])

    season: Optional[str] = None
    for kw, s in SEASON_KEYWORDS.items():
        if kw in p:
            season = s
            break

    budget: Optional[int] = None
    for m in _BUDGET_RE.finditer(p):
        raw = m.group(1).replace(",", "").replace(".", "")
        try:
            val = int(raw)
            if 50 <= val <= 1_000_000:
                budget = max(budget or 0, val)
        except ValueError:
            pass

    duration: Optional[str] = None
    dm = _DURATION_RE.search(p)
    if dm:
        qty_raw = dm.group(1).lower()
        qty     = _NUM_WORDS.get(qty_raw)
        if qty is None:
            try:
                qty = int(qty_raw)
            except ValueError:
                qty = 1
        unit     = dm.group(2).lower()
        duration = f"{qty} {unit}{'s' if qty != 1 else ''}"

    interests: List[str] = []
    for kw, canon in INTEREST_KEYWORDS.items():
        if kw in p and canon not in interests:
            interests.append(canon)

    ctx: Dict[str, Any] = {
        "preferences": prompt,
        "destination": destination,
        "origin":      origin,
        "season":      season,
        "budget_eur":  budget,
        "duration":    duration,
        "interests":   interests,
    }

    L.banner("STEP 1 · CONTEXT EXTRACTION")
    L.row("prompt",       prompt, 300)
    L.row("cities found", cities)
    L.row("origin",       origin)
    L.row("destination",  destination)
    L.row("season",       season)
    L.row("budget_eur",   budget)
    L.row("duration",     duration)
    L.row("interests",    interests)
    L.banner_end()

    return ctx
