"""Approved-template catalog for WhatsApp (Meta UTILITY). WhatsApp is the only outbound channel.

Business-initiated sends use these templates. Placeholder order is the order of
`variables`. WhatsApp bodies use {{1}}..{{n}}. SMS bodies use {#var#} in the
same order. SMS was dropped; sms_text stays an empty legacy column.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TemplateSeed:
    event_key: str
    locale: str
    language_code: str
    whatsapp_template_name: str
    variables: tuple[str, ...]
    body_text: str
    sms_text: str = ""
    category: str = "UTILITY"
    sms_dlt_template_id: str = ""


SAMPLE_VALUES: dict[str, str] = {
    "student_name": "Aarav Sharma",
    "employee_name": "Meera Iyer",
    "date": "11 Oct 2026",
    "school_name": "Sybeez Public School",
    "week_label": "05 Oct 2026 to 11 Oct 2026",
    "present_days": "5",
    "absent_days": "1",
    "late_days": "0",
    "amount": "INR 12500",
    "due_date": "15 Oct 2026",
    "invoice_number": "INV-1042",
    "receipt_number": "RCT-8891",
    "paid_on": "11 Oct 2026",
    "period_label": "Oct 2026",
    "net_pay": "48250",
    "employee_count": "42",
    "lop_days": "2",
    "leave_type": "Paid leave",
    "from_date": "20 Oct 2026",
    "to_date": "21 Oct 2026",
    "title": "School closed Friday",
    "preview": "The campus is closed on 16 Oct for a holiday.",
}

PARENT_EVENTS = frozenset(
    {
        "student_absent",
        "student_late",
        "attendance_weekly_summary",
        "fee_due",
        "fee_overdue",
        "payment_receipt",
        "progress_update",
    }
)
STAFF_EVENTS = frozenset(
    {
        "payslip_published",
        "pay_run_approved",
        "lop_update",
        "leave_approved",
    }
)
URGENT_EVENTS = frozenset({"student_absent", "student_late"})

EVENT_NOTIFICATION_TYPE = {
    "student_absent": "ATTENDANCE_ABSENCE",
    "student_late": "ATTENDANCE_LATE",
    "attendance_weekly_summary": "ATTENDANCE_WEEKLY",
    "fee_due": "FEE_DUE_REMINDER",
    "fee_overdue": "FEE_OVERDUE",
    "payment_receipt": "FEE_RECEIPT_READY",
    "payslip_published": "PAYSLIP_PUBLISHED",
    "pay_run_approved": "PAY_RUN_APPROVED",
    "lop_update": "LOP_UPDATE",
    "leave_approved": "LEAVE_APPROVED",
    "announcement": "ANNOUNCEMENT",
    "progress_update": "PROGRESS_UPDATE",
}


def _seed(event: str, locale: str, variables: tuple[str, ...], body: str, sms: str) -> TemplateSeed:
    return TemplateSeed(
        event_key=event,
        locale=locale,
        language_code=locale,
        whatsapp_template_name=f"sybeez_{event}_{locale}",
        variables=variables,
        body_text=body,
        sms_text="",
    )


_ABSENT = ("student_name", "date", "school_name")
_LATE = _ABSENT
_WEEKLY = ("student_name", "week_label", "present_days", "absent_days", "late_days", "school_name")
_FEE = ("student_name", "amount", "due_date", "invoice_number", "school_name")
_RECEIPT = ("student_name", "amount", "receipt_number", "paid_on", "school_name")
_PAYSLIP = ("employee_name", "period_label", "net_pay", "school_name")
_RUN = ("period_label", "employee_count", "school_name")
_LOP = ("employee_name", "period_label", "lop_days", "school_name")
_LEAVE = ("employee_name", "leave_type", "from_date", "to_date", "school_name")
_ANN = ("title", "preview", "school_name")

DEFAULT_TEMPLATES: tuple[TemplateSeed, ...] = (
    _seed("student_absent", "en", _ABSENT, "{{1}} was absent on {{2}}. - {{3}}", "{#var#} was absent on {#var#}. - {#var#}"),
    _seed("student_absent", "hi", _ABSENT, "{{1}} {{2}} को अनुपस्थित रहे। - {{3}}", "{#var#} {#var#} को अनुपस्थित रहे। - {#var#}"),
    _seed("student_absent", "ta", _ABSENT, "{{1}} {{2}} அன்று பள்ளிக்கு வரவில்லை. - {{3}}", "{#var#} {#var#} அன்று பள்ளிக்கு வரவில்லை. - {#var#}"),
    _seed("student_late", "en", _LATE, "{{1}} arrived late on {{2}}. - {{3}}", "{#var#} arrived late on {#var#}. - {#var#}"),
    _seed("student_late", "hi", _LATE, "{{1}} {{2}} को देर से पहुंचे। - {{3}}", "{#var#} {#var#} को देर से पहुंचे। - {#var#}"),
    _seed("student_late", "ta", _LATE, "{{1}} {{2}} அன்று தாமதமாக வந்தார். - {{3}}", "{#var#} {#var#} அன்று தாமதமாக வந்தார். - {#var#}"),
    _seed(
        "attendance_weekly_summary",
        "en",
        _WEEKLY,
        "Weekly attendance for {{1}} ({{2}}): present {{3}}, absent {{4}}, late {{5}}. - {{6}}",
        "Weekly attendance for {#var#} ({#var#}): present {#var#}, absent {#var#}, late {#var#}. - {#var#}",
    ),
    _seed(
        "attendance_weekly_summary",
        "hi",
        _WEEKLY,
        "{{1}} की साप्ताहिक उपस्थिति ({{2}}): उपस्थित {{3}}, अनुपस्थित {{4}}, विलंब {{5}}। - {{6}}",
        "{#var#} की साप्ताहिक उपस्थिति ({#var#}): उपस्थित {#var#}, अनुपस्थित {#var#}, विलंब {#var#}। - {#var#}",
    ),
    _seed(
        "attendance_weekly_summary",
        "ta",
        _WEEKLY,
        "{{1}} வார வருகை ({{2}}): வந்த நாட்கள் {{3}}, வராத நாட்கள் {{4}}, தாமத நாட்கள் {{5}}. - {{6}}",
        "{#var#} வார வருகை ({#var#}): வந்த நாட்கள் {#var#}, வராத நாட்கள் {#var#}, தாமத நாட்கள் {#var#}. - {#var#}",
    ),
    _seed(
        "fee_due",
        "en",
        _FEE,
        "Fee of {{2}} for {{1}} is due on {{3}}. Invoice {{4}}. - {{5}}",
        "{#var#} has a fee of {#var#} due on {#var#}. Invoice {#var#}. - {#var#}",
    ),
    _seed(
        "fee_due",
        "hi",
        _FEE,
        "{{1}} की फीस {{2}} {{3}} को देय है। चालान {{4}}। - {{5}}",
        "{#var#} की फीस {#var#} {#var#} को देय है। चालान {#var#}। - {#var#}",
    ),
    _seed(
        "fee_due",
        "ta",
        _FEE,
        "{{1}} அவர்களின் கட்டணம் {{2}} {{3}} அன்று செலுத்த வேண்டும். இன்வாய்ஸ் {{4}}. - {{5}}",
        "{#var#} அவர்களின் கட்டணம் {#var#} {#var#} அன்று செலுத்த வேண்டும். இன்வாய்ஸ் {#var#}. - {#var#}",
    ),
    _seed(
        "fee_overdue",
        "en",
        _FEE,
        "Fee of {{2}} for {{1}} is overdue since {{3}}. Invoice {{4}}. - {{5}}",
        "{#var#} has a fee of {#var#} overdue since {#var#}. Invoice {#var#}. - {#var#}",
    ),
    _seed(
        "fee_overdue",
        "hi",
        _FEE,
        "{{1}} की फीस {{2}} {{3}} से अतिदेय है। चालान {{4}}। - {{5}}",
        "{#var#} की फीस {#var#} {#var#} से अतिदेय है। चालान {#var#}। - {#var#}",
    ),
    _seed(
        "fee_overdue",
        "ta",
        _FEE,
        "{{1}} அவர்களின் கட்டணம் {{2}} {{3}} முதல் நிலுவையில் உள்ளது. இன்வாய்ஸ் {{4}}. - {{5}}",
        "{#var#} அவர்களின் கட்டணம் {#var#} {#var#} முதல் நிலுவையில் உள்ளது. இன்வாய்ஸ் {#var#}. - {#var#}",
    ),
    _seed(
        "payment_receipt",
        "en",
        _RECEIPT,
        "Payment of {{2}} received for {{1}}. Receipt {{3}} on {{4}}. - {{5}}",
        "{#var#}: payment of {#var#} received. Receipt {#var#} on {#var#}. - {#var#}",
    ),
    _seed(
        "payment_receipt",
        "hi",
        _RECEIPT,
        "{{1}} के लिए {{2}} का भुगतान मिला। रसीद {{3}}, दिनांक {{4}}। - {{5}}",
        "{#var#} के लिए {#var#} का भुगतान मिला। रसीद {#var#}, दिनांक {#var#}। - {#var#}",
    ),
    _seed(
        "payment_receipt",
        "ta",
        _RECEIPT,
        "{{1}} அவர்களுக்கான {{2}} செலுத்தப்பட்டது. ரசீது {{3}}, தேதி {{4}}. - {{5}}",
        "{#var#} அவர்களுக்கான {#var#} செலுத்தப்பட்டது. ரசீது {#var#}, தேதி {#var#}. - {#var#}",
    ),
    _seed(
        "payslip_published",
        "en",
        _PAYSLIP,
        "{{1}}, your payslip for {{2}} is published. Net pay INR {{3}}. - {{4}}",
        "{#var#}, your payslip for {#var#} is published. Net pay INR {#var#}. - {#var#}",
    ),
    _seed(
        "payslip_published",
        "hi",
        _PAYSLIP,
        "{{1}}, {{2}} की वेतन पर्ची जारी हो गई है। शुद्ध वेतन INR {{3}}। - {{4}}",
        "{#var#}, {#var#} की वेतन पर्ची जारी हो गई है। शुद्ध वेतन INR {#var#}। - {#var#}",
    ),
    _seed(
        "payslip_published",
        "ta",
        _PAYSLIP,
        "{{1}}, {{2}} சம்பள சீட்டு வெளியிடப்பட்டது. நிகர ஊதியம் INR {{3}}. - {{4}}",
        "{#var#}, {#var#} சம்பள சீட்டு வெளியிடப்பட்டது. நிகர ஊதியம் INR {#var#}. - {#var#}",
    ),
    _seed(
        "pay_run_approved",
        "en",
        _RUN,
        "Pay run {{1}} is approved for {{2}} employees. - {{3}}",
        "Pay run {#var#} is approved for {#var#} employees. - {#var#}",
    ),
    _seed(
        "pay_run_approved",
        "hi",
        _RUN,
        "वेतन रन {{1}} {{2}} कर्मचारियों के लिए स्वीकृत है। - {{3}}",
        "वेतन रन {#var#} {#var#} कर्मचारियों के लिए स्वीकृत है। - {#var#}",
    ),
    _seed(
        "pay_run_approved",
        "ta",
        _RUN,
        "{{1}} சம்பள ஓட்டம் {{2}} ஊழியர்களுக்கு அங்கீகரிக்கப்பட்டது. - {{3}}",
        "{#var#} சம்பள ஓட்டம் {#var#} ஊழியர்களுக்கு அங்கீகரிக்கப்பட்டது. - {#var#}",
    ),
    _seed(
        "lop_update",
        "en",
        _LOP,
        "{{1}}, loss of pay for {{2}} is {{3}} day(s). - {{4}}",
        "{#var#}, loss of pay for {#var#} is {#var#} day(s). - {#var#}",
    ),
    _seed(
        "lop_update",
        "hi",
        _LOP,
        "{{1}}, {{2}} के लिए वेतन कटौती {{3}} दिन है। - {{4}}",
        "{#var#}, {#var#} के लिए वेतन कटौती {#var#} दिन है। - {#var#}",
    ),
    _seed(
        "lop_update",
        "ta",
        _LOP,
        "{{1}}, {{2}} காலத்திற்கான ஊதிய இழப்பு {{3}} நாட்கள். - {{4}}",
        "{#var#}, {#var#} காலத்திற்கான ஊதிய இழப்பு {#var#} நாட்கள். - {#var#}",
    ),
    _seed(
        "leave_approved",
        "en",
        _LEAVE,
        "{{1}}, your {{2}} leave from {{3}} to {{4}} is approved. - {{5}}",
        "{#var#}, your {#var#} leave from {#var#} to {#var#} is approved. - {#var#}",
    ),
    _seed(
        "leave_approved",
        "hi",
        _LEAVE,
        "{{1}}, {{3}} से {{4}} तक की {{2}} छुट्टी स्वीकृत है। - {{5}}",
        "{#var#}, {#var#} से {#var#} तक की {#var#} छुट्टी स्वीकृत है। - {#var#}",
    ),
    _seed(
        "leave_approved",
        "ta",
        _LEAVE,
        "{{1}}, {{3}} முதல் {{4}} வரையிலான {{2}} விடுப்பு அங்கீகரிக்கப்பட்டது. - {{5}}",
        "{#var#}, {#var#} முதல் {#var#} வரையிலான {#var#} விடுப்பு அங்கீகரிக்கப்பட்டது. - {#var#}",
    ),
    _seed(
        "announcement",
        "en",
        _ANN,
        "{{1}}: {{2}} - {{3}}",
        "{#var#}: {#var#} - {#var#}",
    ),
    _seed(
        "announcement",
        "hi",
        _ANN,
        "{{1}}: {{2}} - {{3}}",
        "{#var#}: {#var#} - {#var#}",
    ),
    _seed(
        "announcement",
        "ta",
        _ANN,
        "{{1}}: {{2}} - {{3}}",
        "{#var#}: {#var#} - {#var#}",
    ),
)

# Parents choose their own language. The approved WhatsApp template is a language-tagged shell
# ("{{1}}: {{2}} - {{3}}"); the translated title and preview travel in the variables.
NOTICE_LANGUAGES = ("en", "hi", "ta", "te", "kn", "ml", "mr", "bn", "gu", "pa")
DEFAULT_TEMPLATES = DEFAULT_TEMPLATES + tuple(
    _seed(event, code, _ANN, "{{1}}: {{2}} - {{3}}", "")
    for event in ("announcement", "progress_update")
    for code in NOTICE_LANGUAGES
    if not (event == "announcement" and code in {"en", "hi", "ta"})
)


def sanitize_param(value: object) -> str:
    """Meta rejects newlines, tabs, and more than four spaces in a parameter."""
    text = " ".join(str(value if value is not None else "").replace("\n", " ").replace("\t", " ").split())
    return (text[:1024] or "-")


def ordered_params(variables: tuple[str, ...] | list[str], values: dict[str, object]) -> list[str]:
    return [sanitize_param(values.get(name, "")) for name in variables]


def render_message(template_text: str, variables: tuple[str, ...] | list[str], values: dict[str, object]) -> str:
    """Fill {{n}} and {#var#} (in variable order) and {name} tokens."""
    rendered = template_text
    params = ordered_params(variables, values)
    for index, name in enumerate(variables, start=1):
        value = params[index - 1]
        rendered = rendered.replace(f"{{{{{index}}}}}", value)
        rendered = rendered.replace("{#var#}", value, 1)
        rendered = rendered.replace("{" + name + "}", value)
    return rendered


def template_components(variables: tuple[str, ...] | list[str], values: dict[str, object]) -> list[dict]:
    return [
        {
            "type": "body",
            "parameters": [{"type": "text", "text": value} for value in ordered_params(variables, values)],
        }
    ]


def meta_submission_payload(seed: TemplateSeed, samples: dict[str, str] | None = None) -> dict:
    """Body Meta accepts on the message-template create API. Category is UTILITY."""
    examples = samples or SAMPLE_VALUES
    example_row = [sanitize_param(examples.get(name, "sample")) for name in seed.variables]
    return {
        "name": seed.whatsapp_template_name,
        "language": seed.language_code,
        "category": seed.category,
        "components": [
            {
                "type": "BODY",
                "text": seed.body_text,
                "example": {"body_text": [example_row]},
            }
        ],
    }
