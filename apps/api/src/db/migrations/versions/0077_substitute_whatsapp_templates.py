"""Platform WhatsApp templates for substitute cover (substitute_assigned: en, hi, ta).

Revision ID: 0077
Revises: 0076
Create Date: 2026-10-18

Data only. Inert until the school's WhatsApp switch, Meta credentials and the teacher's own opt-in are on.
"""

import json
from typing import Sequence, Union
from uuid import uuid4

from alembic import op
from sqlalchemy import text

revision: str = "0077"
down_revision: Union[str, None] = "0076"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EVENT = "substitute_assigned"


def upgrade() -> None:
    from src.modules.alerts.catalog import DEFAULT_TEMPLATES

    conn = op.get_bind()
    stmt = text(
        """
        INSERT INTO alert_message_templates (
            id, school_id, event_key, locale, whatsapp_template_name, language_code,
            variables, sms_dlt_template_id, sms_text, body_text, category, active
        )
        SELECT CAST(:id AS uuid), NULL, CAST(:event_key AS varchar), CAST(:locale AS varchar),
               CAST(:name AS varchar), CAST(:language_code AS varchar), CAST(:variables AS jsonb), '',
               '', CAST(:body_text AS text), CAST(:category AS varchar), TRUE
        WHERE NOT EXISTS (
            SELECT 1 FROM alert_message_templates
            WHERE school_id IS NULL AND event_key = CAST(:event_key AS varchar) AND locale = CAST(:locale AS varchar)
        )
        """
    )
    for seed in DEFAULT_TEMPLATES:
        if seed.event_key != EVENT:
            continue
        conn.execute(
            stmt,
            {
                "id": str(uuid4()), "event_key": seed.event_key, "locale": seed.locale,
                "name": seed.whatsapp_template_name, "language_code": seed.language_code,
                "variables": json.dumps(list(seed.variables)), "body_text": seed.body_text, "category": seed.category,
            },
        )


def downgrade() -> None:
    op.execute(f"DELETE FROM alert_message_templates WHERE school_id IS NULL AND event_key = '{EVENT}'")
