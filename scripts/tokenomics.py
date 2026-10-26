"""Tokenomics: measure tokens and cost per plan from real runs (needs a Portkey key).

    .venv/bin/python scripts/tokenomics.py

Runs a set of typical requests twice each:
- "fresh": every model call bypasses Portkey's response cache (x-portkey-cache-force-refresh), so
  the numbers are what a new plan really costs;
- "repeat": the identical request again with normal settings. Our Portkey key has response caching
  on, so calls whose input is byte-for-byte the same are answered from Portkey's cache (no OpenAI
  call, no charge). Each call's x-portkey-cache-status header is recorded.
Writes evaluation/tokenomics.json and evaluation/tokenomics.md.
"""
from __future__ import annotations

import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import os  # noqa: E402

import httpx  # noqa: E402
from openai import AsyncOpenAI  # noqa: E402
from pydantic_ai.models.openai import OpenAIResponsesModel  # noqa: E402
from pydantic_ai.providers.openai import OpenAIProvider  # noqa: E402

import route_captain.agents as agents  # noqa: E402
from app import compose_request, finish_form  # noqa: E402
from route_captain.geocode import LANDMARKS  # noqa: E402
from route_captain.llm import MODEL_NAME, PORTKEY_BASE_URL, price, usage_row  # noqa: E402

FORCE_FRESH = [False]
CALL_LOG: dict[int, list[str]] = {}   # id(usage) -> Portkey cache status of each model response


def _model():
    """Same model as the app, plus a hook that records Portkey's cache status for every response."""
    log: list[str] = []

    async def hook(resp):
        log.append(resp.headers.get("x-portkey-cache-status", "?"))
    headers = {"x-portkey-provider": "openai"}
    if FORCE_FRESH[0]:
        headers["x-portkey-cache-force-refresh"] = "true"
    client = AsyncOpenAI(api_key=os.environ["PORTKEY_API_KEY"], base_url=PORTKEY_BASE_URL, default_headers=headers,
                         timeout=120.0, max_retries=2, http_client=httpx.AsyncClient(event_hooks={"response": [hook]}))
    model = OpenAIResponsesModel(MODEL_NAME, provider=OpenAIProvider(openai_client=client))
    model._cache_log = log
    return model


_real_run_agent = agents.run_agent


def _run_agent(agent, prompt, **kw):
    out = _real_run_agent(agent, prompt, **kw)
    CALL_LOG[id(out.usage)] = list(getattr(agent.model, "_cache_log", []))
    return out


def _usage_row(name, usage):
    row = usage_row(name, usage)
    statuses = CALL_LOG.get(id(usage), [])
    row["portkey_cache_hits"] = sum(s == "HIT" for s in statuses)
    misses = max(0, row["requests"] - row["portkey_cache_hits"])
    row["billed_cost_usd"] = (price(row) or 0) * (misses / row["requests"] if row["requests"] else 0)
    return row


agents.build_model, agents.run_agent, agents.usage_row = _model, _run_agent, _usage_row
plan_personal_run = agents.plan_personal_run

CC, SUMMIT = LANDMARKS["Cross Campus"], LANDMARKS["East Rock Summit"]
SAT_9AM = datetime(2026, 10, 31, 9, 0)

# (label, distance, finish mode, stops, interests, finish point, club paces)
TYPICAL = [
    ("5 mi loop", 5, "loop", ["none"], "", None, None),
    ("5 mi ending at a bakery", 5, "at_stop", ["bakery"], "", None, None),
    ("4 mi, coffee halfway", 4, "loop", ["cafe"], "", None, None),
    ("6 mi out & back + water", 6, "out_and_back", ["water"], "", None, None),
    ("4 mi to East Rock Summit", 4, "place", ["none"], "", SUMMIT, None),
    ("5 mi surprise (street art)", 5, "surprise", ["none"], "street art", None, None),
    ("Run club, 3 groups, bakery", 5, "at_stop", ["bakery"], "", None, [8.0, 9.5, 11.0]),
    ("3 mi loop", 3, "loop", ["none"], "", None, None),
]


def run_once(label, dist, mode, stops, interests, finish, club):
    t = time.time()
    res = plan_personal_run(
        compose_request(dist, mode, stops, "", "", interests), CC[0], CC[1], SAT_9AM,
        max(club) if club else 9.5, start_label="Cross Campus",
        end_lat=finish[0] if finish else None, end_lon=finish[1] if finish else None,
        end_label="East Rock Summit" if finish else None,
        form={"distance": dist, **finish_form(mode, stops, interests), "notes": ""}, club_paces=club)
    rows = res.usage
    total = {k: sum(r[k] for r in rows) for k in ("requests", "input_tokens", "cached_input_tokens", "output_tokens",
                                                   "portkey_cache_hits")}
    total["cost_usd"] = price(total)
    total["billed_cost_usd"] = sum(r["billed_cost_usd"] for r in rows)
    return {"label": label, "ok": res.error is None and res.briefing is not None, "seconds": round(time.time() - t, 1),
            "agents": rows, **total}


def main():
    runs = []
    for case in TYPICAL:
        for pass_name in ("fresh", "repeat"):
            FORCE_FRESH[0] = pass_name == "fresh"
            r = run_once(*case)
            r["pass"] = pass_name
            runs.append(r)
            print(f"{pass_name:6} {r['label']:30} in {r['input_tokens']:6} (cached {r['cached_input_tokens']:5}) "
                  f"out {r['output_tokens']:5} calls {r['requests']:2} hits {r['portkey_cache_hits']:2} "
                  f"token cost ${r['cost_usd']:.5f} billed ${r['billed_cost_usd']:.5f} {r['seconds']} s", flush=True)

    def summary(rs):
        return {k: statistics.median(r[k] for r in rs) for k in
                ("input_tokens", "cached_input_tokens", "output_tokens", "requests", "portkey_cache_hits",
                 "cost_usd", "billed_cost_usd", "seconds")} | {
                "mean_cost_usd": statistics.mean(r["cost_usd"] for r in rs),
                "max_cost_usd": max(r["cost_usd"] for r in rs),
                "mean_billed_cost_usd": statistics.mean(r["billed_cost_usd"] for r in rs)}

    cold = summary([r for r in runs if r["pass"] == "fresh"])
    warm = summary([r for r in runs if r["pass"] == "repeat"])
    per_agent = {}
    for r in runs:
        if r["pass"] == "fresh":
            for a in r["agents"]:
                per_agent.setdefault(a["agent"], []).append(a)
    agent_rows = {name: {k: round(statistics.mean(x[k] for x in xs)) for k in
                         ("input_tokens", "cached_input_tokens", "output_tokens", "requests")}
                  | {"runs": len(xs), "max_input_tokens": max(x["input_tokens"] for x in xs),
                     "max_output_tokens": max(x["output_tokens"] for x in xs),
                     "mean_cost_usd": statistics.mean(price(x) or 0 for x in xs)}
                  for name, xs in per_agent.items()}
    out = {"model": MODEL_NAME, "generated": datetime.now().isoformat(timespec="minutes"),
           "prices_per_1m": json.loads((ROOT / "sample_data" / "prices.json").read_text())[MODEL_NAME],
           "fresh": cold, "repeat": warm, "per_agent_fresh_mean": agent_rows, "runs": runs}
    (ROOT / "evaluation" / "tokenomics.json").write_text(json.dumps(out, indent=1), encoding="utf-8")

    lines = ["# Tokenomics (measured)", "",
             f"Generated {out['generated']} by `scripts/tokenomics.py` with {MODEL_NAME} "
             f"(${out['prices_per_1m']['input_per_1m']}/1M input, ${out['prices_per_1m']['cached_input_per_1m']}/1M "
             f"cached input, ${out['prices_per_1m']['output_per_1m']}/1M output).", "",
             "| Pass | Median input | Median cached input | Median output | Model calls | Portkey cache hits "
             "| Median cost (tokens) | Median billed | Median seconds |",
             "|---|---|---|---|---|---|---|---|---|"]
    for name, s in (("Fresh plan", cold), ("Identical repeat", warm)):
        lines.append(f"| {name} | {s['input_tokens']:,.0f} | {s['cached_input_tokens']:,.0f} | {s['output_tokens']:,.0f} "
                     f"| {s['requests']:.0f} | {s['portkey_cache_hits']:.0f} | ${s['cost_usd']:.5f} "
                     f"| ${s['billed_cost_usd']:.5f} | {s['seconds']:.1f} |")
    lines += ["", "Fresh plans per agent (mean over the plans that used it):", "",
              "| Agent | Input | Cached input | Output | Calls | Max input | Max output | Cost | Plans |",
              "|---|---|---|---|---|---|---|---|---|"]
    lines += [f"| {n} | {a['input_tokens']:,} | {a['cached_input_tokens']:,} | {a['output_tokens']:,} | {a['requests']} "
              f"| {a['max_input_tokens']:,} | {a['max_output_tokens']:,} | ${a['mean_cost_usd']:.5f} "
              f"| {a['runs']} of {len(TYPICAL)} |" for n, a in agent_rows.items()]
    lines += ["", "| Pass | Request | Input | Cached | Output | Calls | Cache hits | Cost | Billed | Seconds | OK |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
    lines += [f"| {r['pass']} | {r['label']} | {r['input_tokens']:,} | {r['cached_input_tokens']:,} | {r['output_tokens']:,} "
              f"| {r['requests']} | {r['portkey_cache_hits']} | ${r['cost_usd']:.5f} | ${r['billed_cost_usd']:.5f} "
              f"| {r['seconds']} | {'✅' if r['ok'] else '❌'} |" for r in runs]
    (ROOT / "evaluation" / "tokenomics.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[4:8]))


if __name__ == "__main__":
    main()
