"""Structured outputs exchanged between the agents."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

StopCategory = Literal["none", "bakery", "cafe", "restaurant", "park", "water", "restroom", "track"]


class RunRequest(BaseModel):
    """Planner output: what the runner asked for, in a form the tools can use."""
    distance_miles: float = Field(ge=1, le=26.2, description="Total run distance in miles")
    shape: Literal["loop", "out_and_back", "end_at_stop"] = Field(
        description="loop = finish where you start on a different way back; out_and_back = "
                    "turn around and come back the same way; end_at_stop = finish at the stop")
    stop_category: StopCategory = Field(description="Kind of place to stop at, or none")
    stop_position: Literal["none", "midway", "end"] = Field(
        description="end = finish the run there; midway = stop there and run back")
    extra_stops: list[StopCategory] = Field(
        default_factory=list,
        description="Other kinds of places to pass on the way, besides stop_category (e.g. water, restroom)")
    avoid_streets: list[str] = Field(default_factory=list,
                                     description="Street names the runner wants to avoid, as written")
    runner_notes: str = Field(default="", description="Anything else the runner said, e.g. 'easy day', 'hills'")
    clarification: str | None = Field(
        default=None, description="Only if the request is impossible or unclear: one short question")


class StopChoice(BaseModel):
    """Stops Scout output: which candidate to use. Must be an id from the list given."""
    place_id: str
    backup_place_id: str | None = None
    reason: str = Field(description="One or two sentences, for the runner")


class RunBriefing(BaseModel):
    """Briefing Writer output: the plan in a coach's voice."""
    headline: str = Field(description="One line, e.g. '5.1 mi loop to Atticus Bakery'")
    summary: str = Field(description="2-3 sentences: how the run goes and why this route")
    key_moments: list[str] = Field(description="Up to 4 short cues tied to miles, e.g. 'Mile 2: cross Prospect St at the light'")
    safety_notes: list[str] = Field(description="Short, practical; repeat every safety flag you were given")


class RouteChoice(BaseModel):
    """Navigator output: which candidate route to run. Must be an id from the list given."""
    candidate_id: str
    reason: str = Field(description="One or two sentences for the runner: why this route over the others")


class WeatherAdvice(BaseModel):
    """Weather agent output."""
    summary: str = Field(description="One line, e.g. 'Cool and overcast, 43°F feeling like 37°F'")
    what_to_wear: list[str] = Field(description="3-6 short clothing items for these conditions")
    warnings: list[str] = Field(default_factory=list, description="Every weather warning given, in plain words")
    heat_advice: str | None = Field(default=None, description="Only when heat slows the pace: how much and why")


class PacingPlan(BaseModel):
    """Pacing agent output."""
    strategy: str = Field(description="2 sentences: how to run this route by effort, given its hills")
    hill_tips: list[str] = Field(description="2-4 tips tied to specific mile marks, climbs and target paces")


class CalendarEvent(BaseModel):
    """Calendar agent output, used for Outlook, the .ics file and email."""
    title: str = Field(description="Short event title, at most 60 characters, starting with 'Run:'")
    notes: str = Field(description="At most 500 characters: route, what to wear, the key hill, the stop")
    reminder_minutes: int = Field(ge=0, le=180, description="Minutes before the run to remind the runner")


class BestTime(BaseModel):
    """Scheduler agent output: when to run. Ids must come from the slot list given."""
    slot_id: str
    alternative_ids: list[str] = Field(default_factory=list, description="Up to 2 other good slot ids")
    reason: str = Field(description="1-2 sentences: why this time (weather, daylight, traffic, schedule)")


class DestinationChoice(BaseModel):
    """Destination agent output: a place to finish at that fits the runner's interests."""
    place_id: str
    reason: str = Field(description="One sentence for the runner: why this place fits their interests")


class GroupNote(BaseModel):
    group: str = Field(description="The group's name exactly as given, e.g. 'Group A'")
    note: str = Field(description="1-2 sentences for this group: its pace, how to run the biggest hill, when it arrives")


class ClubBriefing(BaseModel):
    """Run Club Captain output: each group runs its own pace and everyone meets at the finish."""
    summary: str = Field(description="2 sentences: same route, each group at its own pace, where and when the club meets")
    group_notes: list[GroupNote] = Field(description="One note per pace group, in the order given")
    meetup_tips: list[str] = Field(description="2-3 short tips for the meetup at the finish (who arrives first, what to do)")
