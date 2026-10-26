"""Spending guards (no API key needed): a runaway agent, a flood of plans, and a spent daily budget."""
import pytest
from pydantic import BaseModel
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.models.test import TestModel

from route_captain.budget import AGENT_REQUEST_LIMIT, RATE_MESSAGE, SpendGuard
from route_captain.llm import run_agent


class Answer(BaseModel):
    text: str


def test_a_runaway_agent_is_stopped_after_a_few_requests():
    # this agent never accepts its own answer and would retry 50 times without the cap
    agent = Agent(TestModel(), output_type=Answer, retries=50)
    calls = []

    @agent.output_validator
    def never_good_enough(answer: Answer) -> Answer:
        calls.append(1)
        raise ModelRetry("try again")

    with pytest.raises(UsageLimitExceeded):
        run_agent(agent, "plan a run")
    assert len(calls) <= AGENT_REQUEST_LIMIT


def test_one_session_cannot_flood_the_planner():
    guard = SpendGuard(daily_budget_usd=100, plans_per_hour=20, spend_file=None)
    assert all(guard.check("tab-1") is None for _ in range(20))
    assert guard.check("tab-1") == RATE_MESSAGE            # the 21st plan in an hour is refused
    assert guard.check("tab-2") is None                     # other people are unaffected


def test_planning_pauses_when_the_daily_budget_is_spent(tmp_path):
    spend = tmp_path / "spend.json"
    guard = SpendGuard(daily_budget_usd=0.01, plans_per_hour=100, spend_file=spend)
    assert guard.check("tab") is None
    guard.record(0.006)
    assert guard.check("tab") is None
    guard.record(0.006)                                      # now over $0.01
    assert "spending limit" in guard.check("tab")
    # a restart remembers today's spend
    assert "spending limit" in SpendGuard(daily_budget_usd=0.01, plans_per_hour=100, spend_file=spend).check("tab")
