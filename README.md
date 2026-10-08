# n8n workflow -> FastAPI

This project replaces the supplied n8n workflow with a small FastAPI service.

The runtime flow is:

1. Receive a completed call payload.
2. Build the Russian analysis prompt from `analysis.summary`, `transcript`, and `phone_number`.
3. Call OpenRouter using the configured model and validate the structured JSON response.
4. Map the extracted fields to the STOCRM offer payload.
5. Skip the STOCRM request when there is no phone number.
6. POST the offer to `https://STOCRM_HOST/api/external/v1/offer/new/with_contact`.

## Files

- `main.py` - FastAPI routes and workflow orchestration.
- `llm.py` - OpenRouter request, structured output schema, and retries.
- `stocrm.py` - field mapping, phone check, and STOCRM request.
- `models.py` - input/output models and the original Russian prompt.
- `config.py` - environment-based configuration.
- `sample_payload.json` - reduced replay fixture based on the supplied pin data.
- `replay.py` - local command-line replay tool.

## Setup

```bash
uv sync
cp env.example .env
```

Set at least these values in `.env`:

```dotenv
OPENROUTER_API_KEY=sk-or-v1-your-key
STOCRM_SID=your-current-stocrm-sid
```

The SID from the workflow was included in the uploaded JSON. Treat it as exposed and rotate it before production use.

For the first test, use:

```dotenv
DRY_RUN=true
```

With `DRY_RUN=true`, the OpenRouter extraction runs, but no offer is sent to STOCRM.

## Run

```bash
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

The drop-in webhook route is:

```text
POST /okolomota-ctosrm/webhook/add-offer
```

It accepts the call JSON directly. It also accepts an n8n-style wrapper where the actual call payload is under `body` only if you send it through `/process` after unwrapping in your proxy; the recommended integration sends the body directly.

For a synchronous call:

```bash
curl -X POST 'http://127.0.0.1:8000/okolomota-ctosrm/webhook/add-offer' \
  -H 'Content-Type: application/json' \
  --data-binary @sample_payload.json
```

For immediate HTTP acknowledgement and background processing, append `?wait=false`.

## Replay locally

The replay tool prints the exact prompt, extracted fields, and STOCRM request result:

```bash
DRY_RUN=true uv run replay.py
```

Only run the LLM extraction:

```bash
uv run replay.py --extract-only
```

## Webhook signature

Signature checking is disabled when `SHARED_SECRET` is empty. To enable the current implementation:

```dotenv
SHARED_SECRET=your-shared-secret
```

Then send `x-yapogovoru-signature: sha256=<hex-hmac>` where the HMAC-SHA256 input is the raw request body and the key is `SHARED_SECRET`.

Confirm the exact signing formula with the webhook provider before enabling this in production; some providers include the timestamp in the signed bytes.

## Production notes

- Put the service behind HTTPS and a reverse proxy.
- Keep `.env` out of version control.
- Use `wait=false` only when the caller accepts asynchronous processing.
- Add an idempotency store keyed by `call_id` or `conversation_id` if the provider can redeliver events. The n8n workflow itself does not implement deduplication.
- Keep `DRY_RUN=true` until the generated STOCRM payload has been reviewed.
