"""Replaces "Map Ticket Fields1" (Set node) + "Has Phone?1" (IF)
+ "Create Offer in STOCRM1" (HTTP Request node).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from pydantic import json

from config import Settings
from models import AgentOutput, CallPayload

log = logging.getLogger("call-pipeline.stocrm")

OFFER_PATH = "/api/external/v1/offer/new/with_contact"


def has_phone(ticket: dict[str, Any]) -> bool:
    """The n8n IF node: `{{ $json.phone }}` notEmpty (strict, case sensitive)."""
    phone = ticket.get("phone")
    return bool(phone and str(phone).strip())


def map_ticket_fields(
    payload: CallPayload, agent_output: AgentOutput, settings: Settings
) -> dict[str, Any]:
    """The Set node. Note the phone rule matches n8n: it takes ONLY the LLM's
    phone. The caller-ID fallback is an opt-in extra (see settings)."""
    ticket = {
        "phone": agent_output.phone.strip(),
        "title": "FARES TEST" + agent_output.client.strip(),
        "comment": "FARES TEST " + agent_output.result,
        # Bikes can be phrased without a brand.
        "moto": agent_output.brand,
        "Model": agent_output.bike_model,
        "request": agent_output.request,
        "time": agent_output.preferred_time,
    }

    if not ticket["phone"] and settings.phone_fallback_to_caller_id:
        fallback = payload.phone_number or (payload.customer_data or {}).get("phone")
        if fallback:
            log.info("LLM gave no phone, falling back to caller ID %s", fallback)
            ticket["phone"] = str(fallback).strip()
            ticket["phone_source"] = "caller_id"
    else:
        ticket["phone_source"] = "llm"

    return ticket


def build_offer_body(ticket: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Same JSON the n8n HTTP node builds, including the COMMENT layout."""
    comment = (
        f"{ticket.get('comment', '')}"
        f"\n\nМотоцикл: {ticket.get('moto', '')} {ticket.get('Model', '')}"
        f"\nЗапрос: {ticket.get('request', '')}"
        f"\nУдобное время: {ticket.get('time', '')}"
    )
    return {
        "SID": settings.stocrm_sid,
        "PHONE": ticket["phone"],
        "TITLE": ticket.get("title", ""),
        "SOURCE_ID": settings.stocrm_source_id,  # integer in n8n (unquoted)
        "BOARD_ID": settings.stocrm_board_id,  # integer in n8n (unquoted)
        "COMMENT": comment,
    }


async def create_offer(ticket: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """POST to STOCRM. Returns a result dict instead of raising — the n8n node
    has onError=continueErrorOutput, so a failed ticket must not kill the run."""
    body = build_offer_body(ticket, settings)
    url = f"https://{settings.stocrm_host}{OFFER_PATH}"
    if settings.dry_run:
        log.info("DRY_RUN — would POST %s: %s", url, body)
        return {"ok": True, "dry_run FOO": True, "url": url, "request": body}
    try:
        async with httpx.AsyncClient(timeout=settings.stocrm_timeout) as client:
            response = await client.post(url, json=body)
        text = response.text
        ok = response.is_success
        try:
            parsed = response.json()
        except ValueError:
            parsed = None
        if not ok:
            log.error(
                "STOCRM returned %s for call: %s", response.status_code, text[:500]
            )
        return {
            "ok": ok,
            "status_code": response.status_code,
            "url": url,
            "request": body,
            "response": parsed if parsed is not None else text[:2000],
        }
    except httpx.HTTPError as exc:
        log.error("STOCRM request failed: %s", exc)
        return {"ok": False, "url": url, "request": body, "error": str(exc)}
