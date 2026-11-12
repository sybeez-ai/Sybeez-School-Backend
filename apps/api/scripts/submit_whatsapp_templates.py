#!/usr/bin/env python3
"""Submit the seeded UTILITY templates to Meta once a token exists.

Run from apps/api:

    python scripts/submit_whatsapp_templates.py
    python scripts/submit_whatsapp_templates.py --dry-run

Reads WHATSAPP_ACCESS_TOKEN and WHATSAPP_BUSINESS_ACCOUNT_ID from the
environment or from Secrets Manager (ALERTS_SECRETS_ARN or
WHATSAPP_SECRETS_ARN). Prints "WhatsApp not configured" and exits 0 when
either value is missing. Does not print the token.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from src.core.config import Settings  # noqa: E402
from src.modules.alerts.catalog import DEFAULT_TEMPLATES, meta_submission_payload  # noqa: E402
from src.modules.alerts.credentials import WHATSAPP_NOT_CONFIGURED  # noqa: E402
from src.modules.alerts.submit import default_post_json, submit_message_templates  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    dry_run = "--dry-run" in args
    settings = Settings()
    payloads = [meta_submission_payload(seed) for seed in DEFAULT_TEMPLATES]
    result = submit_message_templates(
        payloads,
        access_token=settings.whatsapp_cloud_access_token,
        waba_id=settings.whatsapp_business_account_id,
        post_json=default_post_json,
        dry_run=dry_run,
    )
    print(json.dumps(result, indent=2))
    if result["status"] == WHATSAPP_NOT_CONFIGURED:
        return 0
    failed = [item for item in result["submitted"] if item.get("error")]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
