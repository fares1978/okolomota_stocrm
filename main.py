"""FastAPI port of the n8n workflow "ОколоМота / call -> STOCRM offer".

Pipeline (same order as the workflow graph):
    webhook body
      -> build prompt from transcript ......... llm.build_prompt
      -> OpenRouter structured extraction ..... llm.extract_call_data   (AI Agent1)
      -> map ticket fields .................... stocrm.map_ticket_fields (Set node)
      -> phone not empty? ..................... stocrm.has_phone         (IF node)
      -> POST /offer/new/with_contact ......... stocrm.create_offer      (HTTP node)

Run:  uvicorn main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import sys
from contextlib import asynccontextmanager
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse

import llm
import stocrm
from config import get_settings
from models import CallPayload, ProcessResult

settings = get_settings()

logging.basicConfig(
    level=settings.log_level.upper(),
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
)
log = logging.getLogger("call-pipeline")


# --------------------------------------------------------------------------- #
# Signature check (the caller sends x-yapogovoru-signature: sha256=<hmac>)
# --------------------------------------------------------------------------- #
def verify_signature(
    raw_body: bytes, signature: str | None, timestamp: str | None
) -> None:
    if not settings.shared_secret:
        return  # verification disabled
    if not signature:
        raise HTTPException(status_code=401, detail="missing signature")

    expected = hmac.new(
        settings.shared_secret.encode(), raw_body, hashlib.sha256
    ).hexdigest()
    provided = signature.split("=", 1)[1] if "=" in signature else signature
    if not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=401, detail="bad signature")
    if timestamp:
        log.debug("signed request at %s", timestamp)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info(
        "STOCRM host=%s board=%s source=%s | model=%s | dry_run=%s",
        settings.stocrm_host,
        settings.stocrm_board_id,
        settings.stocrm_source_id,
        settings.openrouter_model,
        settings.dry_run,
    )
    if not settings.openrouter_api_key:
        log.warning("OPENROUTER_API_KEY is empty — extraction will fail")
    if not settings.stocrm_sid:
        log.warning("STOCRM_SID is empty — offers will be rejected")
    yield
    log.info("shutting down")


app = FastAPI(title="ОколоМота call pipeline", version="1.0.0", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# Core handler
# --------------------------------------------------------------------------- #
async def process_call(payload: CallPayload) -> ProcessResult:
    result = ProcessResult(
        call_id=payload.call_id, conversation_id=payload.conversation_id
    )

    # --- AI Agent1 ---------------------------------------------------------
    try:
        agent_output = await llm.extract_call_data(payload, settings)
    except llm.LLMError as exc:
        result.status = "error"
        result.reason = "extraction_failed"
        result.errors.append(str(exc))
        return result

    result.agent_output = agent_output.as_russian_dict()

    # --- Set node ----------------------------------------------------------
    ticket = stocrm.map_ticket_fields(payload, agent_output, settings)
    result.ticket = ticket

    # --- IF node -----------------------------------------------------------
    if not stocrm.has_phone(ticket):
        result.status = "skipped"
        result.reason = "no_phone"
        log.warning("call_id=%s skipped: no phone in ticket", payload.call_id)
        return result

    print("ticket to send to STOCRM:", json.dumps(ticket, ensure_ascii=False, indent=2))

    # --- HTTP Request node (onError = continueErrorOutput) ------------------
    result.stocrm = await stocrm.create_offer(ticket, settings)
    if not result.stocrm.get("ok"):
        result.status = "error"
        result.reason = "stocrm_failed"
        result.errors.append(
            str(result.stocrm.get("error") or result.stocrm.get("status_code"))
        )
    return result


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.post("/okolomota-ctosrm/webhook/add-offer", response_model=ProcessResult)
async def webhook_nalog_taxi(
    request: Request,
    background_tasks: BackgroundTasks,
    wait: bool = Query(
        True, description="false = reply immediately, work in background"
    ),
    x_yapogovoru_signature: str | None = Header(None, alias="x-yapogovoru-signature"),
    x_yapogovoru_timestamp: str | None = Header(None, alias="x-yapogovoru-timestamp"),
):
    """Drop-in replacement for the n8n webhook URL."""
    raw = await request.body()
    verify_signature(raw, x_yapogovoru_signature, x_yapogovoru_timestamp)
    payload = CallPayload.model_validate_json(raw)

    if not wait:
        background_tasks.add_task(_run_and_log, payload)
        return ProcessResult(
            status="ok",
            call_id=payload.call_id,
            conversation_id=payload.conversation_id,
            reason="accepted",
        )

    return await process_call(payload)


async def _run_and_log(payload: CallPayload) -> None:
    try:
        result = await process_call(payload)
        log.info("background result: %s", result.model_dump(exclude_none=True))
    except Exception:  # never let a background task die silently
        log.exception("background processing crashed (call_id=%s)", payload.call_id)


@app.post("/process", response_model=ProcessResult)
async def process_direct(payload: CallPayload):
    """Same logic, no signature check — handy for tests and internal calls."""
    return await process_call(payload)


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "model": settings.openrouter_model,
        "stocrm_host": settings.stocrm_host,
        "dry_run": settings.dry_run,
    }


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error on %s", request.url.path)
    return JSONResponse(
        status_code=500, content={"status": "error", "errors": [str(exc)]}
    )
