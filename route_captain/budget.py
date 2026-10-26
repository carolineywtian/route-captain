"""Spending guards: stop a bug or a bad actor from burning through the model budget.

Three layers (the first lives in llm.run_agent):
1. Per agent call: at most AGENT_REQUEST_LIMIT model requests and AGENT_TOKEN_LIMIT tokens
   (pydantic-ai UsageLimits), so a looping or runaway agent stops instead of spending.
2. Per browser session: at most PLANS_PER_HOUR plans in any rolling hour.
3. Whole app: at most DAILY_BUDGET_USD of model spend per day (set ROUTE_CAPTAIN_DAILY_BUDGET_USD to
   change it). Spend is saved to sample_data/.spend.json so restarting the app doesn't reset it.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import date
from pathlib import Path

AGENT_REQUEST_LIMIT = 3        # one try + two validation retries
AGENT_TOKEN_LIMIT = 15_000     # about 7x the largest agent call measured (scripts/tokenomics.py)
PLANS_PER_HOUR = 20
DAILY_BUDGET_USD = float(os.getenv("ROUTE_CAPTAIN_DAILY_BUDGET_USD", "1.00"))
SPEND_FILE = Path(__file__).resolve().parent.parent / "sample_data" / ".spend.json"

RATE_MESSAGE = (f"You've planned {PLANS_PER_HOUR} runs in the last hour, the most one person can plan. "
                "Please try again in a few minutes.")


class SpendGuard:
    def __init__(self, daily_budget_usd: float = DAILY_BUDGET_USD, plans_per_hour: int = PLANS_PER_HOUR,
                 spend_file: Path | None = SPEND_FILE):
        self.daily_budget_usd, self.plans_per_hour, self.spend_file = daily_budget_usd, plans_per_hour, spend_file
        self._lock = threading.Lock()
        self._plans: dict[str, list[float]] = {}
        self._day, self._spent = date.today().isoformat(), 0.0
        if spend_file and spend_file.exists():
            try:
                saved = json.loads(spend_file.read_text(encoding="utf-8"))
                if saved.get("day") == self._day:
                    self._spent = float(saved.get("spent_usd", 0.0))
            except (ValueError, OSError):
                pass

    def _roll_day(self):
        today = date.today().isoformat()
        if today != self._day:
            self._day, self._spent = today, 0.0

    def check(self, session: str) -> str | None:
        """None if this session may plan now; otherwise a plain message saying why not."""
        with self._lock:
            self._roll_day()
            if self._spent >= self.daily_budget_usd:
                return (f"Route Captain has reached today's AI spending limit (${self.daily_budget_usd:.2f}), "
                        "so planning is paused until tomorrow. Nothing was charged for this request.")
            now = time.time()
            recent = [t for t in self._plans.get(session, []) if now - t < 3600]
            self._plans[session] = recent
            if len(recent) >= self.plans_per_hour:
                return RATE_MESSAGE
            recent.append(now)
            return None

    def record(self, cost_usd: float | None):
        """Add one plan's model cost (token count x price; cached answers are counted too, to be safe)."""
        if not cost_usd:
            return
        with self._lock:
            self._roll_day()
            self._spent += cost_usd
            if self.spend_file:
                try:
                    self.spend_file.write_text(json.dumps({"day": self._day, "spent_usd": round(self._spent, 6)}),
                                               encoding="utf-8")
                except OSError:
                    pass

    @property
    def spent_today(self) -> float:
        with self._lock:
            self._roll_day()
            return self._spent


GUARD = SpendGuard()
