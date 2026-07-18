"""One bounded public-planning call for AgentLand's API mode."""

import re

from agents import Agent, RunConfig, Runner
from agents.models.openai_responses import OpenAIResponsesModel
from openai import AsyncOpenAI


MODEL_ALLOWLIST = ("gpt-5.4-mini", "gpt-5.6-sol")
DEFAULT_MODEL = "gpt-5.4-mini"


def valid_model(model):
    return model in MODEL_ALLOWLIST


def _clean(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()[:500]


def _usage(result):
    input_tokens = output_tokens = 0
    for response in getattr(result, "raw_responses", []):
        usage = getattr(response, "usage", None)
        input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
        output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
    return {"input_tokens": input_tokens, "output_tokens": output_tokens}


def run_mission(*, session_id, api_key, model, mission):
    """Return a public orchestration note only; Builder/Test evidence remains local."""
    if not api_key:
        return {"ok": False, "error_category": "api_not_configured"}
    if not valid_model(model):
        return {"ok": False, "error_category": "model_unavailable"}
    try:
        client = AsyncOpenAI(api_key=api_key, max_retries=1, timeout=35)
        api_model = OpenAIResponsesModel(model, client)
        agent = Agent(
            name="Orchestrator",
            model=api_model,
            instructions=(
                "Return only a short public plan for the user's mission. "
                "The available bounded workflow is: assign Builder, repair room isolation, "
                "then run the fixed Tester verification. Never claim a file or test succeeded. "
                "Do not reveal private reasoning."
            ),
        )
        result = Runner.run_sync(
            agent,
            "Mission: {0}\nSession: {1}".format(_clean(mission)[:1000], session_id),
            max_turns=1,
            run_config=RunConfig(model=api_model, tracing_disabled=True, trace_include_sensitive_data=False),
        )
        return {"ok": True, "public_summary": _clean(result.final_output), "provider": "openai", "model": model, "usage": _usage(result)}
    except Exception as error:
        name = error.__class__.__name__.lower()
        category = "authentication_failed" if "auth" in name else ("rate_limited" if "rate" in name else ("model_unavailable" if "model" in name else "api_unavailable"))
        return {"ok": False, "error_category": category}
