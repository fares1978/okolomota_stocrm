"""Pydantic models + the LLM contract (prompt text and output schema).

Mirrors three n8n nodes: the trigger payload, the "AI Agent1" contract and
the "Map Ticket Fields1" Set node.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------- #
# Incoming webhook payload (the n8n Trigger node's body)
# --------------------------------------------------------------------------- #
class TranscriptTurn(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: Literal["agent", "user", "system"] | str
    message: str | None = None
    time_seconds: int | None = None


class CallPayload(BaseModel):
    """Loose on purpose — the caller may add fields and we must not 422."""

    model_config = ConfigDict(extra="allow")

    call_id: int | None = None
    conversation_id: str | None = None
    agent_name: str | None = None
    direction: str | None = None
    status: str | None = None
    failure_reason: str | None = None
    duration_seconds: int | None = None
    attempt_number: int | None = None
    retry_scheduled: bool | None = None
    duration_human: str | None = None
    phone_number: str | None = None
    customer_data: dict[str, Any] | None = None
    transcript: list[TranscriptTurn] = Field(default_factory=list)
    analysis: dict[str, Any] = Field(default_factory=dict)
    recording_url: str | None = None
    created_at: datetime | None = None
    processed_at: datetime | None = None


# --------------------------------------------------------------------------- #
# LLM structured output (the "Structured Output Parser1" node)
# --------------------------------------------------------------------------- #
class AgentOutput(BaseModel):
    """Keys are the Russian ones from the n8n jsonSchemaExample; the Python
    attribute names are ASCII aliases so the code stays readable."""

    model_config = ConfigDict(populate_by_name=True, extra="allow")

    result: str = Field("", description="Call result / short outcome")
    client: str = Field("", alias="Клиент")
    phone: str = Field("", alias="телефон")
    brand: str = Field("", alias="марка")
    bike_model: str = Field("", alias="модель")
    request: str = Field("", alias="запрос")
    preferred_time: str = Field("", alias="Удобное время")

    def get(self, key: str, default: str = "") -> str:
        """Access by the original Russian key."""
        try:
            value = getattr(self, self._ALIAS_TO_ATTR.get(key, key))
        except AttributeError:
            return default
        return "" if value is None else str(value)

    _ALIAS_TO_ATTR = {
        "result": "result",
        "Клиент": "client",
        "телефон": "phone",
        "марка": "brand",
        "модель": "bike_model",
        "запрос": "request",
        "Удобное время": "preferred_time",
    }

    def as_russian_dict(self) -> dict[str, str]:
        return {
            "result": self.result,
            "Клиент": self.client,
            "телефон": self.phone,
            "марка": self.brand,
            "модель": self.bike_model,
            "запрос": self.request,
            "Удобное время": self.preferred_time,
        }


# JSON Schema handed to OpenRouter for structured outputs.
AGENT_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "result": {"type": "string", "description": "Краткий итог звонка"},
        "Клиент": {"type": "string", "description": "Имя клиента или 'не назвал'"},
        "телефон": {"type": "string", "description": "Телефон в формате +7XXXXXXXXXX"},
        "марка": {"type": "string", "description": "Марка мотоцикла"},
        "модель": {"type": "string", "description": "Модель мотоцикла"},
        "запрос": {"type": "string", "description": "Что нужно сделать"},
        "Удобное время": {"type": "string", "description": "Когда клиенту удобно"},
    },
    "required": [
        "result",
        "Клиент",
        "телефон",
        "марка",
        "модель",
        "запрос",
        "Удобное время",
    ],
    "additionalProperties": False,
}


# The "AI Agent1" system prompt, verbatim from the workflow.
PROMPT_HEADER = """Ты — аналитик входящих звонков мотосервиса ОколоМота.

Прочитай транскрипцию звонка и напиши одно короткое сообщение для менеджера с деталями обращения.

Формат — свободный текст, например:

«Клиент [имя или "не назвал"], тел. [номер]. Мотоцикл: [марка, модель, год]. Запрос: [что нужно сделать]. Удобное время: [когда]. Мессенджер: [Телеграм / Вотсап / Макс]. Требует подтверждения менеджером.»

Если что-то не прозвучало — пропусти это поле.
Если заявка не была оформлена — напиши коротко, зачем звонил клиент и чем завершился разговор.
Никогда не пиши «заявка подтверждена» — подтверждение всегда остаётся за менеджером.
"""

PROMPT_TEMPLATE = PROMPT_HEADER + """
summary: {summary}
транскрипция: {transcript}
телефон: {phone}
"""


# --------------------------------------------------------------------------- #
# API response shape
# --------------------------------------------------------------------------- #
class ProcessResult(BaseModel):
    status: Literal["ok", "skipped", "error"] = "ok"
    call_id: int | None = None
    conversation_id: str | None = None
    reason: str | None = None
    agent_output: dict[str, str] | None = None
    ticket: dict[str, Any] | None = None
    stocrm: dict[str, Any] | None = None
    errors: list[str] = Field(default_factory=list)
