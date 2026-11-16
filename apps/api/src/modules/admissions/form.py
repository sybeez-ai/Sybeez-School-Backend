"""The public enquiry form uses a fixed set of columns on admission_enquiries.

School admins choose the intro and which of those fields are shown. They cannot
add columns. Student name stays on every form.
"""

from __future__ import annotations

FIELD_KEYS = ("student_name", "parent_name", "phone", "email", "notes")

_DEFAULTS: tuple[dict, ...] = (
    {"key": "student_name", "label": "Student name", "required": True},
    {"key": "parent_name", "label": "Parent name", "required": False},
    {"key": "phone", "label": "Phone", "required": False},
    {"key": "email", "label": "Email", "required": False},
    {"key": "notes", "label": "Message", "required": False},
)


def normalize_form(raw: dict | None) -> dict:
    labels = {item["key"]: item["label"] for item in _DEFAULTS}
    required = {item["key"]: item["required"] for item in _DEFAULTS}
    enabled = {item["key"]: True for item in _DEFAULTS}
    intro = ""
    if isinstance(raw, dict):
        intro = str(raw.get("intro") or "").strip()[:400]
        fields = raw.get("fields")
        if isinstance(fields, list):
            for item in fields:
                if not isinstance(item, dict):
                    continue
                key = str(item.get("key") or "")
                if key not in labels:
                    continue
                enabled[key] = bool(item.get("enabled", True))
                label = str(item.get("label") or "").strip()
                if label:
                    labels[key] = label[:80]
                if key != "student_name":
                    required[key] = bool(item.get("required", required[key]))
    enabled["student_name"] = True
    required["student_name"] = True
    return {
        "intro": intro,
        "fields": [
            {
                "key": item["key"],
                "label": labels[item["key"]],
                "enabled": enabled[item["key"]],
                "required": required[item["key"]] if enabled[item["key"]] else False,
            }
            for item in _DEFAULTS
        ],
    }


def public_fields(form: dict) -> list[dict]:
    return [field for field in form["fields"] if field["enabled"]]


def honeypot_tripped(value: str | None) -> bool:
    return bool((value or "").strip())


def missing_required(form: dict, payload: dict) -> list[str]:
    missing: list[str] = []
    for field in form["fields"]:
        if not field["enabled"] or not field["required"]:
            continue
        if not str(payload.get(field["key"]) or "").strip():
            missing.append(str(field["label"]))
    return missing
