"""Local end-to-end check without touching STOCRM for real.

    DRY_RUN=true python replay.py                 # full pipeline, offer only logged
    python replay.py --extract-only               # LLM step only

Requires the app to be importable from the current directory.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

logging.basicConfig(level="INFO", format="%(levelname)-7s %(name)s | %(message)s")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default="sample_payload.json")
    parser.add_argument("--extract-only", action="store_true")
    args = parser.parse_args()

    from config import get_settings
    from llm import build_prompt, extract_call_data
    from models import CallPayload

    settings = get_settings()

    def load_payload(path: str) -> CallPayload:
        with open(path, encoding="utf-8") as payload_file:
            return CallPayload.model_validate_json(payload_file.read())

    payload = await asyncio.to_thread(load_payload, args.file)

    print("=" * 70)
    print("PROMPT SENT TO THE MODEL")
    print("=" * 70)
    print(build_prompt(payload))
    print("=" * 70)

    agent_output = await extract_call_data(payload, settings)
    print(
        "AGENT OUTPUT:",
        json.dumps(agent_output.as_russian_dict(), ensure_ascii=False, indent=2),
    )

    if args.extract_only:
        return 0

    from stocrm import create_offer, has_phone, map_ticket_fields

    ticket = map_ticket_fields(payload, agent_output, settings)
    print("TICKET:", json.dumps(ticket, ensure_ascii=False, indent=2))

    if not has_phone(ticket):
        print("SKIPPED — the IF node would have stopped here (no phone).")
        return 0

    result = await create_offer(ticket, settings)
    print("STOCRM RESULT:", json.dumps(result, ensure_ascii=False, indent=2)[:1500])
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
