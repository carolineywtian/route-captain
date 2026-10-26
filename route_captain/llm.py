"""Model access through Portkey, plus token and cost accounting."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

from dotenv import dotenv_values
from openai import AsyncOpenAI
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
PLACEHOLDER = "your-portkey-key-here"


def load_env_files(folders=(PROJECT, PROJECT.parent)) -> None:
    """Read .env from the repo's own folder first, then the folder above it (where the teaching
    staff keep theirs). A value already set in the environment wins; empty or placeholder values
    are skipped, so a copied-but-unedited .env.example in the repo can't hide the real key above."""
    for folder in folders:
        path = Path(folder) / ".env"
        if not path.is_file():
            continue
        for name, value in dotenv_values(path).items():
            value = (value or "").strip()
            if value and value != PLACEHOLDER and not os.environ.get(name, "").strip():
                os.environ[name] = value


load_env_files()

MODEL_NAME = "gpt-6-luna"  # the only model allowed on the course Portkey key
PORTKEY_BASE_URL = "https://api.portkey.ai/v1"
PRICES_PATH = HERE.parent / "sample_data" / "prices.json"


KEY_MISSING = ("No Portkey key found. Put PORTKEY_API_KEY=your key in a .env file next to app.py or in the "
               "folder above it (copy .env.example to start), then restart the app.")


def has_key() -> bool:
    key = os.getenv("PORTKEY_API_KEY", "").strip()
    return bool(key) and key != PLACEHOLDER


def build_model() -> OpenAIResponsesModel:
    if not has_key():
        raise RuntimeError(KEY_MISSING)
    key = os.getenv("PORTKEY_API_KEY", "").strip()
    client = AsyncOpenAI(api_key=key, base_url=PORTKEY_BASE_URL,
                         default_headers={"x-portkey-provider": "openai"},
                         timeout=120.0, max_retries=2)
    return OpenAIResponsesModel(MODEL_NAME, provider=OpenAIProvider(openai_client=client))


def run_agent(agent, prompt: str, **kwargs):
    """Run an agent and close its connection to the model before its event loop ends.

    (Letting connections close on their own later prints "Event loop is closed" warnings.)"""
    from pydantic_ai.usage import UsageLimits

    from .budget import AGENT_REQUEST_LIMIT, AGENT_TOKEN_LIMIT
    # every agent call is capped, so a looping or runaway agent stops instead of spending
    kwargs.setdefault("usage_limits", UsageLimits(request_limit=AGENT_REQUEST_LIMIT,
                                                  total_tokens_limit=AGENT_TOKEN_LIMIT))

    async def go():
        try:
            return await agent.run(prompt, **kwargs)
        finally:
            client = getattr(agent.model, "client", None)
            if client is not None:
                await client.close()
    return asyncio.run(go())


def usage_row(agent: str, usage) -> dict:
    """Token counts for one agent run, priced from sample_data/prices.json when available."""
    row = {
        "agent": agent,
        "requests": usage.requests,
        "input_tokens": usage.input_tokens,
        "cached_input_tokens": usage.cache_read_tokens,
        "output_tokens": usage.output_tokens,
    }
    row["cost_usd"] = price(row)
    return row


def price(row: dict) -> float | None:
    try:
        p = json.loads(PRICES_PATH.read_text(encoding="utf-8"))[MODEL_NAME]
    except (FileNotFoundError, KeyError):
        return None
    if p.get("input_per_1m") is None:
        return None
    uncached = row["input_tokens"] - row["cached_input_tokens"]
    return (uncached * p["input_per_1m"] + row["cached_input_tokens"] * p["cached_input_per_1m"]
            + row["output_tokens"] * p["output_per_1m"]) / 1_000_000
