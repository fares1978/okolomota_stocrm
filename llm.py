"""Replaces the n8n langchain nodes: "OpenRouter Chat Model1",
"Structured Output Parser1" and "AI Agent1" (with its retryOnFail=3, 2s wait).
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx

from config import Settings
from models import AGENT_OUTPUT_SCHEMA, PROMPT_TEMPLATE, AgentOutput, CallPayload

log = logging.getLogger("call-pipeline.llm")


class LLMError(RuntimeError):
    pass


def build_prompt(payload: CallPayload) -> str:
    """Rebuild the n8n expression:

        транскрипция: {{ $json.body.transcript.map(t => `[${t.role}] ${t.message}`).join('\n') }}

    Two differences from n8n: `null` messages are dropped (the sample payload
    ends with empty agent turns, which n8n would render as "[agent] null"),
    and `summary` falls back gracefully when analysis is missing.
    """
    lines = [
        f"[{turn.role}] {turn.message}"
        for turn in payload.transcript
        if turn.message  # skip null / empty turns
    ]
    summary = (payload.analysis or {}).get("summary") or "нет"
    phone = (
        payload.phone_number
        or (payload.customer_data or {}).get("phone")
        or "не определён"
    )

    return PROMPT_TEMPLATE.format(
        summary=summary,
        transcript="\n".join(lines) if lines else "нет",
        phone=phone,
    )


def _parse_content(content: str) -> dict:
    """Tolerate models that wrap JSON in ``` fences despite the schema."""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("```")[1]
        text = text[4:] if text.lower().startswith("json") else text
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:  # last resort: grab the outer object
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
        raise LLMError(f"Model returned non-JSON: {content[:400]!r}") from exc


async def _call_once(
    client: httpx.AsyncClient, settings: Settings, prompt: str
) -> AgentOutput:
    response = await client.post(
        "/chat/completions",
        json={
            "model": settings.openrouter_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "call_analysis",
                    "strict": True,
                    "schema": AGENT_OUTPUT_SCHEMA,
                },
            },
            # Providers that ignore response_format get filtered out.
            "provider": {"require_parameters": True},
        },
    )
    response.raise_for_status()
    body = response.json()

    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise LLMError(f"Unexpected completion shape: {body}") from exc

    return AgentOutput.model_validate(_parse_content(content))


async def extract_call_data(payload: CallPayload, settings: Settings) -> AgentOutput:
    """Call the model, retrying like the n8n agent node does."""
    if not settings.openrouter_api_key:
        raise LLMError("OPENROUTER_API_KEY is not set")

    prompt = build_prompt(payload)
    headers = {
        "Authorization": f"Bearer {settings.openrouter_api_key}",
        # Optional attribution headers OpenRouter uses for its dashboard.
        "HTTP-Referer": f"https://{settings.stocrm_host}",
        "X-Title": "OkoloMota call pipeline",
    }

    last_error: Exception | None = None
    async with httpx.AsyncClient(
        base_url=settings.openrouter_base_url,
        headers=headers,
        timeout=settings.llm_timeout,
    ) as client:
        for attempt in range(1, settings.llm_max_attempts + 1):
            try:
                result = await _call_once(client, settings, prompt)
                log.info("LLM ok on attempt %s (call_id=%s)", attempt, payload.call_id)
                return result
            except (httpx.HTTPError, LLMError, ValueError) as exc:
                last_error = exc
                log.warning(
                    "LLM attempt %s/%s failed (call_id=%s): %s",
                    attempt,
                    settings.llm_max_attempts,
                    payload.call_id,
                    exc,
                )
                if attempt < settings.llm_max_attempts:
                    await asyncio.sleep(settings.llm_retry_delay)

    raise LLMError(f"All {settings.llm_max_attempts} attempts failed: {last_error}")
