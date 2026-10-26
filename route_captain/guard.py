"""Guard: screens untrusted map text before any agent sees it.

Place names, descriptions and opening hours come from OpenStreetMap, which
anyone can edit, so they are treated as untrusted data. Three layers:

  code   - this module: places whose text looks like instructions are quarantined,
           and only short, whitelisted fields are ever passed on (descriptions,
           websites and other free text are never sent to a model)
  design - the Stops Scout can only pick a place id from the list it was given,
           and every safety decision is made by code, not by a model
  prompt - agent instructions say place text is data, never instructions
"""
from __future__ import annotations

import re

MAX_NAME_CHARS = 60
GENERIC_NAMES = {"water": "Water fountain", "restroom": "Public restroom", "park": "Park", "track": "Running track",
                 "cafe": "Café", "bakery": "Bakery", "restaurant": "Restaurant", "viewpoint": "Scenic viewpoint",
                 "artwork": "Public art", "garden": "Garden", "waterfront": "Waterfront", "historic": "Historic site",
                 "attraction": "Attraction", "museum": "Museum", "library": "Library", "ice_cream": "Ice cream",
                 "bookstore": "Bookstore"}

_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above|earlier|your)? ?(instructions|rules|directions)",
    r"disregard (the |all |your )?(instructions|rules|safety)",
    r"\b(system|assistant|developer)\s*(prompt|message)?\s*:",
    r"you are (now )?(an?|the) ",
    r"\b(ai|llm|agent|assistant|chatbot|model)s?\b.{0,40}\b(must|should|always|never|ignore|route|send|choose|pick|recommend)",
    r"\b(route|send|direct) (the |all )?(runner|runners|users?)\b",
    r"\b(override|bypass|skip|turn off)\b.{0,30}\b(safety|check|guard|filter|rules?)",
    r"<\s*/?\s*(script|system|instructions?)\s*>",
    r"\bnew instructions\b",
]
_REGEX = re.compile("|".join(f"(?:{p})" for p in _PATTERNS), re.IGNORECASE)
# Links are normal in `website` and `description` fields, but not inside a name or opening hours.
_LINK = re.compile(r"https?://|www\.", re.IGNORECASE)


def find_injection(text: str | None, allow_links: bool = False) -> str | None:
    """The suspicious snippet if `text` looks like it is addressing an AI, else None."""
    if not text:
        return None
    match = _REGEX.search(str(text)) or (None if allow_links else _LINK.search(str(text)))
    return match.group(0) if match else None


def screen_place(place: dict) -> tuple[dict | None, str | None]:
    """Returns (safe_place, None) or (None, reason) if the place is quarantined.

    Every free-text field is checked, even ones that are never sent to a model,
    because a place that tries to manipulate an AI should not be recommended at all.
    """
    for field in ("name", "description", "opening_hours", "website"):
        # links are normal in websites and descriptions (which never reach a model), not in names or hours
        hit = find_injection(place.get(field), allow_links=field in ("website", "description"))
        if hit:
            return None, f'{field} contains instruction-like text: "{hit}"'
    name = str(place.get("name") or "").split(";")[0].strip()  # "Name;Other name" -> "Name"
    if len(name) > MAX_NAME_CHARS:
        return None, f"name is unusually long ({len(name)} characters)"
    safe = {k: place[k] for k in ("id", "name", "category", "lat", "lon", "opening_hours") if k in place}
    if not name:  # many fountains and restrooms have no name in the map data
        safe["unnamed"] = True
    safe["name"] = name or GENERIC_NAMES.get(place.get("category"), "Unnamed place")
    return safe, None
