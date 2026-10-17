# WhatsApp alerts (Meta Cloud API)

Sybeez sends business-initiated WhatsApp messages with Meta-approved UTILITY templates through the WhatsApp Business Cloud API. WhatsApp is the only outbound channel: SMS and MSG91 were removed. Parents opt in themselves in the parent portal and choose their language; the alert goes out in that language. Free text is used only inside the 24-hour customer service window after the parent or staff member messages the school number.

Platform default templates are seeded for ten locales: `en`, `hi`, `ta`, `te`, `kn`, `ml`, `mr`, `bn`, `gu`, `pa` (notices and progress updates; the other events keep `en`, `hi`, `ta`). For notices the approved template is a language-tagged shell `{{1}}: {{2}} - {{3}}`; the translated title and preview travel in the variables. A school can override a template. WhatsApp stays off until a school admin enables it, Meta credentials exist, and the parent has opted in.

Silver already has a dedicated number, a WhatsApp Business account on the mobile app, a Meta developer account, and a created app. Meta Business verification is still pending, so there is no access token and no app secret yet. The API and the worker start cleanly in that state. `GET /api/v1/alerts/status` returns `whatsapp_status: "WhatsApp not configured"` and the sender stays on the mock provider. Nothing is posted to Meta, and a missing secret does not crash the process.

## Where credentials are read

Every credential is read from the process environment, or from one JSON secret in AWS Secrets Manager referenced by `ALERTS_SECRETS_ARN` or `WHATSAPP_SECRETS_ARN`. Environment variables win when both are set. The repository does not contain a live token.

| Credential | Environment variable |
| --- | --- |
| Permanent token | `WHATSAPP_ACCESS_TOKEN` (`WHATSAPP_CLOUD_ACCESS_TOKEN` is still accepted) |
| Cloud API phone number id | `WHATSAPP_PHONE_NUMBER_ID` (`WHATSAPP_CLOUD_PHONE_NUMBER_ID` is still accepted) |
| WhatsApp Business Account id | `WHATSAPP_BUSINESS_ACCOUNT_ID` |
| Meta app secret | `WHATSAPP_APP_SECRET` |
| Webhook verify token | `WHATSAPP_VERIFY_TOKEN` |

`whatsapp_status` stays `WhatsApp not configured` until the access token, phone number id, business account id, app secret, and verify token are all present.  Setting `WHATSAPP_PROVIDER=meta_cloud` before those values exist still sends through mock. After the values exist, set:

```
WHATSAPP_PROVIDER=meta_cloud
```

`gupshup` and `twilio` are stub adapters and do not send.

## Business verification

The legal name on the Meta Business account is **Sybis Technology Private Limited**. Meta compares that name with the public website. Put the same legal name, spelled the same way, on the website footer, the contact page, and the privacy page. A marketing name alone on those pages is a common reason verification stays pending. Verification has to finish before Meta issues a permanent system-user token or shows a production app secret for this WhatsApp Business Account.

## Cloud API number

The dedicated number is on the WhatsApp Business mobile app today. The Cloud API cannot send from a number that stays only on that app. Before `WHATSAPP_PHONE_NUMBER_ID` exists, do one of these:

- Migrate the number off the WhatsApp Business app and onto the Cloud API. The handset loses the WhatsApp Business app for that number.
- Complete Meta coexistence onboarding so the same number stays on the WhatsApp Business app and on the Cloud API.

After either step, copy the Cloud API phone number id into `WHATSAPP_PHONE_NUMBER_ID` and the WhatsApp Business Account id into `WHATSAPP_BUSINESS_ACCOUNT_ID`.

## What to give the platform after approval

| Approval | What to give the platform | Environment variable |
| --- | --- | --- |
| Meta Business verification | Verified Meta Business Manager for Sybis Technology Private Limited, with the WhatsApp Business Account linked | — |
| WhatsApp number | The dedicated number on the Cloud API (migrated, or coexistence), and its phone number id | `WHATSAPP_PHONE_NUMBER_ID` |
| Business account id | WhatsApp Business Account id from WhatsApp Manager | `WHATSAPP_BUSINESS_ACCOUNT_ID` |
| Permanent token | A system-user token with `whatsapp_business_messaging` and `whatsapp_business_management` | `WHATSAPP_ACCESS_TOKEN` |
| App secret | The Meta app secret used to check `X-Hub-Signature-256` | `WHATSAPP_APP_SECRET` |
| Verify token | The string you type into the Meta webhook subscription | `WHATSAPP_VERIFY_TOKEN` |
| Template approvals | Submit every JSON object below in category UTILITY. Wait until each name is approved before enabling WhatsApp for a school. Names must match exactly. | stored per template as `whatsapp_template_name` |

After the secrets are present, a school admin enables WhatsApp with `PUT /api/v1/alerts/settings`. Quiet hours default to 21:00–07:00 Asia/Kolkata. Student absent and student late bypass quiet hours. Other alerts wait until 07:00. Parents still need to opt in (`parent_message_preferences.whatsapp_opt_in`, set from the parent portal). `STOP` opts that phone out.

Webhook callback URLs:

- `GET` and `POST /api/v1/webhooks/whatsapp/meta`

## Submitting templates

Templates can be prepared now and submitted the moment a token and a WhatsApp Business Account id exist. The JSON in this file is the body Meta accepts. Category is UTILITY.

Three ways to submit, all using the same names and bodies:

1. Script, from `apps/api`, once the token and business account id are in the environment or Secrets Manager:

```
python scripts/submit_whatsapp_templates.py --dry-run
python scripts/submit_whatsapp_templates.py
```

`--dry-run` prints the JSON and does not call Meta. With the token or the business account id missing, the script prints `WhatsApp not configured` and exits 0.

2. Admin endpoint `POST /api/v1/alerts/templates/submit`. Query `dry_run=true` to return the JSON. Query `event_key` and `locale` to submit a subset. The endpoint posts to `https://graph.facebook.com/v20.0/{WHATSAPP_BUSINESS_ACCOUNT_ID}/message_templates`. The same missing-credential response is `{"status": "WhatsApp not configured"}` with an empty `submitted` list.

3. WhatsApp Manager, by hand:

- Finish business verification for Sybis Technology Private Limited.
- Add the number with Cloud API migration or coexistence onboarding, as above.
- In the Meta app, add the WhatsApp product. In Business settings, create a system user, assign this app and the WhatsApp account, and generate a permanent token with `whatsapp_business_messaging` and `whatsapp_business_management`.
- Open WhatsApp Manager → Message templates → Create template.
- Category: Utility. Name and language must match the JSON (`sybeez_{event}_{locale}`, language `en`, `hi`, `ta`, `te`, `kn`, `ml`, `mr`, `bn`, `gu`, or `pa`). Paste the BODY text. Enter the sample row from `example.body_text`. Submit each locale.
- Wait until each template is approved before a school admin sets `whatsapp_enabled` to true.
- App settings → Basic → App secret becomes `WHATSAPP_APP_SECRET`.
- WhatsApp → Configuration → Webhook: callback `https://<api-host>/api/v1/webhooks/whatsapp/meta`, verify token equal to `WHATSAPP_VERIFY_TOKEN`, subscribe to `messages`.

## Endpoints for DeliveryLogView

The frontend `DeliveryLogView` should call these school-admin routes. Session role is `SCHOOL_ADMIN` or `PLATFORM_ADMIN`. Platform admins pass `X-School-Id`. Render `to_masked` in the table. `to` is available for support. The older `GET /api/v1/communication/deliveries` list remains.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/v1/alerts/deliveries` | List. Query: `channel`, `status`, `event` (event key or notification type), `date_from`, `date_to`, `recipient` (user id or phone fragment), `limit`, `offset`. Response `{ items, total }`. |
| GET | `/api/v1/alerts/deliveries/{id}` | One delivery, including `sent_at`, `delivered_at`, `read_at`, `failed_at`, `error_code`, `template_name`. |
| POST | `/api/v1/alerts/deliveries/{id}/resend` | Re-queue a `FAILED` delivery. |
| GET | `/api/v1/alerts/status` | `whatsapp_status` is `WhatsApp not configured` or `configured`. Also the effective provider and the env names still blank. |
| GET | `/api/v1/alerts/settings` | Per-school enable flags, quiet hours, timezone, default locale, plus the same provider status fields. |
| PUT | `/api/v1/alerts/settings` | Update any of `whatsapp_enabled`, `quiet_hours_start`, `quiet_hours_end` (`HH:MM`), `timezone`, `default_locale`. |
| GET | `/api/v1/alerts/templates` | Effective templates. School rows override platform defaults. Query: `event_key`, `locale`. |
| POST | `/api/v1/alerts/templates` | Create a school-specific template. |
| POST | `/api/v1/alerts/templates/submit` | Submit active templates to the WABA `message_templates` API. `dry_run=true` returns the JSON. Missing token or business account id returns status `WhatsApp not configured`. |
| PUT | `/api/v1/alerts/templates/{id}` | Update a school template. Editing a platform default copies it onto the school first. |

Delivery item fields: `id`, `school_id`, `user_id`, `channel`, `notification_type`, `event_key`, `to`, `to_masked`, `title`, `body`, `provider`, `provider_message_id`, `status`, `attempts`, `last_error`, `error_code`, `locale`, `template_name`, `scheduled_for`, `sent_at`, `delivered_at`, `read_at`, `failed_at`, `created_at`.

Status values: `PENDING`, `SCHEDULED`, `SENT`, `DELIVERED`, `READ`, `FAILED`.

## Behaviour

- Parents receive student progress updates, student absent, student late, weekly attendance, fee due, fee overdue, and payment receipt. Payroll staff receive pay-run approved. The employee receives payslip published, LOP update, and staff leave approved. Student leave approved goes to parents. Announcements go to the parents of the announcement's class (or all parents for a school-wide notice). Progress updates go to that student's parents.
- WhatsApp is sent when the school has it on, Meta credentials exist, the parent opted in, and the number has not sent STOP. There is no SMS fallback. A failed send (including Meta errors such as 131026, not a WhatsApp user) stays `FAILED` and can be resent from the delivery log.
- Duplicate sends with the same school, channel, and dedupe key are skipped unless the earlier row failed.
- Inbound WhatsApp messages are stored. `STOP`, `STOPALL`, `UNSUBSCRIBE`, `CANCEL`, `END`, `QUIT`, and `OPT OUT` record an opt-out and turn the preference off.
- Phone numbers are masked in logs and in `alert_audit_logs`. New tables use forced row-level security on `school_id`. Platform default templates (`school_id` null) are readable by the school. Global opt-outs are readable so a STOP is honored.
- The worker consumes the Redis list `sybeez:jobs`. `JOBS_BACKEND=sqs` with `JOBS_SQS_QUEUE_URL` sends and polls SQS instead. The provider rate limit defaults to 60 WhatsApp messages per minute (`WHATSAPP_RATE_LIMIT_PER_MINUTE`).
- A job tagged `meta_cloud` is sent as mock when the credentials are blank. The worker logs `WhatsApp not configured` and does not call Graph.
- Scheduled quiet-hour rows are released by `POST /internal/v1/alerts/release-due`. Weekly summaries are `POST /internal/v1/attendance/{school_id}/digests/weekly/run`.

## Meta template JSON

Submit each object to the WhatsApp message template API. Category is UTILITY. Do not change the name or the `{{n}}` order.

### student_absent

A parent is told a child was marked absent.

Locale `en`. Variables, in order: student_name, date, school_name.

```json
{
  "name": "sybeez_student_absent_en",
  "language": "en",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}} was absent on {{2}}. - {{3}}",
      "example": {
        "body_text": [
          [
            "Aarav Sharma",
            "11 Oct 2026",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `hi`. Variables, in order: student_name, date, school_name.

```json
{
  "name": "sybeez_student_absent_hi",
  "language": "hi",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}} {{2}} को अनुपस्थित रहे। - {{3}}",
      "example": {
        "body_text": [
          [
            "Aarav Sharma",
            "11 Oct 2026",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `ta`. Variables, in order: student_name, date, school_name.

```json
{
  "name": "sybeez_student_absent_ta",
  "language": "ta",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}} {{2}} அன்று பள்ளிக்கு வரவில்லை. - {{3}}",
      "example": {
        "body_text": [
          [
            "Aarav Sharma",
            "11 Oct 2026",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

### student_late

A parent is told a child arrived late.

Locale `en`. Variables, in order: student_name, date, school_name.

```json
{
  "name": "sybeez_student_late_en",
  "language": "en",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}} arrived late on {{2}}. - {{3}}",
      "example": {
        "body_text": [
          [
            "Aarav Sharma",
            "11 Oct 2026",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `hi`. Variables, in order: student_name, date, school_name.

```json
{
  "name": "sybeez_student_late_hi",
  "language": "hi",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}} {{2}} को देर से पहुंचे। - {{3}}",
      "example": {
        "body_text": [
          [
            "Aarav Sharma",
            "11 Oct 2026",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `ta`. Variables, in order: student_name, date, school_name.

```json
{
  "name": "sybeez_student_late_ta",
  "language": "ta",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}} {{2}} அன்று தாமதமாக வந்தார். - {{3}}",
      "example": {
        "body_text": [
          [
            "Aarav Sharma",
            "11 Oct 2026",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

### attendance_weekly_summary

A parent receives the week's present, absent, and late counts.

Locale `en`. Variables, in order: student_name, week_label, present_days, absent_days, late_days, school_name.

```json
{
  "name": "sybeez_attendance_weekly_summary_en",
  "language": "en",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "Weekly attendance for {{1}} ({{2}}): present {{3}}, absent {{4}}, late {{5}}. - {{6}}",
      "example": {
        "body_text": [
          [
            "Aarav Sharma",
            "05 Oct 2026 to 11 Oct 2026",
            "5",
            "1",
            "0",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `hi`. Variables, in order: student_name, week_label, present_days, absent_days, late_days, school_name.

```json
{
  "name": "sybeez_attendance_weekly_summary_hi",
  "language": "hi",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}} की साप्ताहिक उपस्थिति ({{2}}): उपस्थित {{3}}, अनुपस्थित {{4}}, विलंब {{5}}। - {{6}}",
      "example": {
        "body_text": [
          [
            "Aarav Sharma",
            "05 Oct 2026 to 11 Oct 2026",
            "5",
            "1",
            "0",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `ta`. Variables, in order: student_name, week_label, present_days, absent_days, late_days, school_name.

```json
{
  "name": "sybeez_attendance_weekly_summary_ta",
  "language": "ta",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}} வார வருகை ({{2}}): வந்த நாட்கள் {{3}}, வராத நாட்கள் {{4}}, தாமத நாட்கள் {{5}}. - {{6}}",
      "example": {
        "body_text": [
          [
            "Aarav Sharma",
            "05 Oct 2026 to 11 Oct 2026",
            "5",
            "1",
            "0",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

### fee_due

A parent is reminded a fee is due.

Locale `en`. Variables, in order: student_name, amount, due_date, invoice_number, school_name.

```json
{
  "name": "sybeez_fee_due_en",
  "language": "en",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "Fee of {{2}} for {{1}} is due on {{3}}. Invoice {{4}}. - {{5}}",
      "example": {
        "body_text": [
          [
            "Aarav Sharma",
            "INR 12500",
            "15 Oct 2026",
            "INV-1042",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `hi`. Variables, in order: student_name, amount, due_date, invoice_number, school_name.

```json
{
  "name": "sybeez_fee_due_hi",
  "language": "hi",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}} की फीस {{2}} {{3}} को देय है। चालान {{4}}। - {{5}}",
      "example": {
        "body_text": [
          [
            "Aarav Sharma",
            "INR 12500",
            "15 Oct 2026",
            "INV-1042",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `ta`. Variables, in order: student_name, amount, due_date, invoice_number, school_name.

```json
{
  "name": "sybeez_fee_due_ta",
  "language": "ta",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}} அவர்களின் கட்டணம் {{2}} {{3}} அன்று செலுத்த வேண்டும். இன்வாய்ஸ் {{4}}. - {{5}}",
      "example": {
        "body_text": [
          [
            "Aarav Sharma",
            "INR 12500",
            "15 Oct 2026",
            "INV-1042",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

### fee_overdue

A parent is told a fee is overdue.

Locale `en`. Variables, in order: student_name, amount, due_date, invoice_number, school_name.

```json
{
  "name": "sybeez_fee_overdue_en",
  "language": "en",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "Fee of {{2}} for {{1}} is overdue since {{3}}. Invoice {{4}}. - {{5}}",
      "example": {
        "body_text": [
          [
            "Aarav Sharma",
            "INR 12500",
            "15 Oct 2026",
            "INV-1042",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `hi`. Variables, in order: student_name, amount, due_date, invoice_number, school_name.

```json
{
  "name": "sybeez_fee_overdue_hi",
  "language": "hi",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}} की फीस {{2}} {{3}} से अतिदेय है। चालान {{4}}। - {{5}}",
      "example": {
        "body_text": [
          [
            "Aarav Sharma",
            "INR 12500",
            "15 Oct 2026",
            "INV-1042",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `ta`. Variables, in order: student_name, amount, due_date, invoice_number, school_name.

```json
{
  "name": "sybeez_fee_overdue_ta",
  "language": "ta",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}} அவர்களின் கட்டணம் {{2}} {{3}} முதல் நிலுவையில் உள்ளது. இன்வாய்ஸ் {{4}}. - {{5}}",
      "example": {
        "body_text": [
          [
            "Aarav Sharma",
            "INR 12500",
            "15 Oct 2026",
            "INV-1042",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

### payment_receipt

A parent receives a payment receipt.

Locale `en`. Variables, in order: student_name, amount, receipt_number, paid_on, school_name.

```json
{
  "name": "sybeez_payment_receipt_en",
  "language": "en",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "Payment of {{2}} received for {{1}}. Receipt {{3}} on {{4}}. - {{5}}",
      "example": {
        "body_text": [
          [
            "Aarav Sharma",
            "INR 12500",
            "RCT-8891",
            "11 Oct 2026",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `hi`. Variables, in order: student_name, amount, receipt_number, paid_on, school_name.

```json
{
  "name": "sybeez_payment_receipt_hi",
  "language": "hi",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}} के लिए {{2}} का भुगतान मिला। रसीद {{3}}, दिनांक {{4}}। - {{5}}",
      "example": {
        "body_text": [
          [
            "Aarav Sharma",
            "INR 12500",
            "RCT-8891",
            "11 Oct 2026",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `ta`. Variables, in order: student_name, amount, receipt_number, paid_on, school_name.

```json
{
  "name": "sybeez_payment_receipt_ta",
  "language": "ta",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}} அவர்களுக்கான {{2}} செலுத்தப்பட்டது. ரசீது {{3}}, தேதி {{4}}. - {{5}}",
      "example": {
        "body_text": [
          [
            "Aarav Sharma",
            "INR 12500",
            "RCT-8891",
            "11 Oct 2026",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

### payslip_published

An employee is told their payslip is published.

Locale `en`. Variables, in order: employee_name, period_label, net_pay, school_name.

```json
{
  "name": "sybeez_payslip_published_en",
  "language": "en",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}, your payslip for {{2}} is published. Net pay INR {{3}}. - {{4}}",
      "example": {
        "body_text": [
          [
            "Meera Iyer",
            "Oct 2026",
            "48250",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `hi`. Variables, in order: employee_name, period_label, net_pay, school_name.

```json
{
  "name": "sybeez_payslip_published_hi",
  "language": "hi",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}, {{2}} की वेतन पर्ची जारी हो गई है। शुद्ध वेतन INR {{3}}। - {{4}}",
      "example": {
        "body_text": [
          [
            "Meera Iyer",
            "Oct 2026",
            "48250",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `ta`. Variables, in order: employee_name, period_label, net_pay, school_name.

```json
{
  "name": "sybeez_payslip_published_ta",
  "language": "ta",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}, {{2}} சம்பள சீட்டு வெளியிடப்பட்டது. நிகர ஊதியம் INR {{3}}. - {{4}}",
      "example": {
        "body_text": [
          [
            "Meera Iyer",
            "Oct 2026",
            "48250",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

### pay_run_approved

Payroll staff are told a pay run was approved.

Locale `en`. Variables, in order: period_label, employee_count, school_name.

```json
{
  "name": "sybeez_pay_run_approved_en",
  "language": "en",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "Pay run {{1}} is approved for {{2}} employees. - {{3}}",
      "example": {
        "body_text": [
          [
            "Oct 2026",
            "42",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `hi`. Variables, in order: period_label, employee_count, school_name.

```json
{
  "name": "sybeez_pay_run_approved_hi",
  "language": "hi",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "वेतन रन {{1}} {{2}} कर्मचारियों के लिए स्वीकृत है। - {{3}}",
      "example": {
        "body_text": [
          [
            "Oct 2026",
            "42",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `ta`. Variables, in order: period_label, employee_count, school_name.

```json
{
  "name": "sybeez_pay_run_approved_ta",
  "language": "ta",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}} சம்பள ஓட்டம் {{2}} ஊழியர்களுக்கு அங்கீகரிக்கப்பட்டது. - {{3}}",
      "example": {
        "body_text": [
          [
            "Oct 2026",
            "42",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

### lop_update

An employee is told their loss-of-pay days.

Locale `en`. Variables, in order: employee_name, period_label, lop_days, school_name.

```json
{
  "name": "sybeez_lop_update_en",
  "language": "en",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}, loss of pay for {{2}} is {{3}} day(s). - {{4}}",
      "example": {
        "body_text": [
          [
            "Meera Iyer",
            "Oct 2026",
            "2",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `hi`. Variables, in order: employee_name, period_label, lop_days, school_name.

```json
{
  "name": "sybeez_lop_update_hi",
  "language": "hi",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}, {{2}} के लिए वेतन कटौती {{3}} दिन है। - {{4}}",
      "example": {
        "body_text": [
          [
            "Meera Iyer",
            "Oct 2026",
            "2",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `ta`. Variables, in order: employee_name, period_label, lop_days, school_name.

```json
{
  "name": "sybeez_lop_update_ta",
  "language": "ta",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}, {{2}} காலத்திற்கான ஊதிய இழப்பு {{3}} நாட்கள். - {{4}}",
      "example": {
        "body_text": [
          [
            "Meera Iyer",
            "Oct 2026",
            "2",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

### leave_approved

An employee, or a parent for student leave, is told leave was approved.

Locale `en`. Variables, in order: employee_name, leave_type, from_date, to_date, school_name.

```json
{
  "name": "sybeez_leave_approved_en",
  "language": "en",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}, your {{2}} leave from {{3}} to {{4}} is approved. - {{5}}",
      "example": {
        "body_text": [
          [
            "Meera Iyer",
            "Paid leave",
            "20 Oct 2026",
            "21 Oct 2026",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `hi`. Variables, in order: employee_name, leave_type, from_date, to_date, school_name.

```json
{
  "name": "sybeez_leave_approved_hi",
  "language": "hi",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}, {{3}} से {{4}} तक की {{2}} छुट्टी स्वीकृत है। - {{5}}",
      "example": {
        "body_text": [
          [
            "Meera Iyer",
            "Paid leave",
            "20 Oct 2026",
            "21 Oct 2026",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `ta`. Variables, in order: employee_name, leave_type, from_date, to_date, school_name.

```json
{
  "name": "sybeez_leave_approved_ta",
  "language": "ta",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}, {{3}} முதல் {{4}} வரையிலான {{2}} விடுப்பு அங்கீகரிக்கப்பட்டது. - {{5}}",
      "example": {
        "body_text": [
          [
            "Meera Iyer",
            "Paid leave",
            "20 Oct 2026",
            "21 Oct 2026",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

### announcement

A published announcement.

Locale `en`. Variables, in order: title, preview, school_name.

```json
{
  "name": "sybeez_announcement_en",
  "language": "en",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}: {{2}} - {{3}}",
      "example": {
        "body_text": [
          [
            "School closed Friday",
            "The campus is closed on 16 Oct for a holiday.",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `hi`. Variables, in order: title, preview, school_name.

```json
{
  "name": "sybeez_announcement_hi",
  "language": "hi",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}: {{2}} - {{3}}",
      "example": {
        "body_text": [
          [
            "School closed Friday",
            "The campus is closed on 16 Oct for a holiday.",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `ta`. Variables, in order: title, preview, school_name.

```json
{
  "name": "sybeez_announcement_ta",
  "language": "ta",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}: {{2}} - {{3}}",
      "example": {
        "body_text": [
          [
            "School closed Friday",
            "The campus is closed on 16 Oct for a holiday.",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `te`. Variables, in order: title, preview, school_name.

```json
{
  "name": "sybeez_announcement_te",
  "language": "te",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}: {{2}} - {{3}}",
      "example": {
        "body_text": [
          [
            "School closed Friday",
            "The campus is closed on 16 Oct for a holiday.",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `kn`. Variables, in order: title, preview, school_name.

```json
{
  "name": "sybeez_announcement_kn",
  "language": "kn",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}: {{2}} - {{3}}",
      "example": {
        "body_text": [
          [
            "School closed Friday",
            "The campus is closed on 16 Oct for a holiday.",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `ml`. Variables, in order: title, preview, school_name.

```json
{
  "name": "sybeez_announcement_ml",
  "language": "ml",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}: {{2}} - {{3}}",
      "example": {
        "body_text": [
          [
            "School closed Friday",
            "The campus is closed on 16 Oct for a holiday.",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `mr`. Variables, in order: title, preview, school_name.

```json
{
  "name": "sybeez_announcement_mr",
  "language": "mr",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}: {{2}} - {{3}}",
      "example": {
        "body_text": [
          [
            "School closed Friday",
            "The campus is closed on 16 Oct for a holiday.",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `bn`. Variables, in order: title, preview, school_name.

```json
{
  "name": "sybeez_announcement_bn",
  "language": "bn",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}: {{2}} - {{3}}",
      "example": {
        "body_text": [
          [
            "School closed Friday",
            "The campus is closed on 16 Oct for a holiday.",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `gu`. Variables, in order: title, preview, school_name.

```json
{
  "name": "sybeez_announcement_gu",
  "language": "gu",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}: {{2}} - {{3}}",
      "example": {
        "body_text": [
          [
            "School closed Friday",
            "The campus is closed on 16 Oct for a holiday.",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `pa`. Variables, in order: title, preview, school_name.

```json
{
  "name": "sybeez_announcement_pa",
  "language": "pa",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}: {{2}} - {{3}}",
      "example": {
        "body_text": [
          [
            "School closed Friday",
            "The campus is closed on 16 Oct for a holiday.",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

### progress_update

A parent is told about their child's progress or results update from a teacher.

Locale `en`. Variables, in order: title, preview, school_name.

```json
{
  "name": "sybeez_progress_update_en",
  "language": "en",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}: {{2}} - {{3}}",
      "example": {
        "body_text": [
          [
            "School closed Friday",
            "The campus is closed on 16 Oct for a holiday.",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `hi`. Variables, in order: title, preview, school_name.

```json
{
  "name": "sybeez_progress_update_hi",
  "language": "hi",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}: {{2}} - {{3}}",
      "example": {
        "body_text": [
          [
            "School closed Friday",
            "The campus is closed on 16 Oct for a holiday.",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `ta`. Variables, in order: title, preview, school_name.

```json
{
  "name": "sybeez_progress_update_ta",
  "language": "ta",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}: {{2}} - {{3}}",
      "example": {
        "body_text": [
          [
            "School closed Friday",
            "The campus is closed on 16 Oct for a holiday.",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `te`. Variables, in order: title, preview, school_name.

```json
{
  "name": "sybeez_progress_update_te",
  "language": "te",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}: {{2}} - {{3}}",
      "example": {
        "body_text": [
          [
            "School closed Friday",
            "The campus is closed on 16 Oct for a holiday.",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `kn`. Variables, in order: title, preview, school_name.

```json
{
  "name": "sybeez_progress_update_kn",
  "language": "kn",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}: {{2}} - {{3}}",
      "example": {
        "body_text": [
          [
            "School closed Friday",
            "The campus is closed on 16 Oct for a holiday.",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `ml`. Variables, in order: title, preview, school_name.

```json
{
  "name": "sybeez_progress_update_ml",
  "language": "ml",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}: {{2}} - {{3}}",
      "example": {
        "body_text": [
          [
            "School closed Friday",
            "The campus is closed on 16 Oct for a holiday.",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `mr`. Variables, in order: title, preview, school_name.

```json
{
  "name": "sybeez_progress_update_mr",
  "language": "mr",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}: {{2}} - {{3}}",
      "example": {
        "body_text": [
          [
            "School closed Friday",
            "The campus is closed on 16 Oct for a holiday.",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `bn`. Variables, in order: title, preview, school_name.

```json
{
  "name": "sybeez_progress_update_bn",
  "language": "bn",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}: {{2}} - {{3}}",
      "example": {
        "body_text": [
          [
            "School closed Friday",
            "The campus is closed on 16 Oct for a holiday.",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `gu`. Variables, in order: title, preview, school_name.

```json
{
  "name": "sybeez_progress_update_gu",
  "language": "gu",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}: {{2}} - {{3}}",
      "example": {
        "body_text": [
          [
            "School closed Friday",
            "The campus is closed on 16 Oct for a holiday.",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```

Locale `pa`. Variables, in order: title, preview, school_name.

```json
{
  "name": "sybeez_progress_update_pa",
  "language": "pa",
  "category": "UTILITY",
  "components": [
    {
      "type": "BODY",
      "text": "{{1}}: {{2}} - {{3}}",
      "example": {
        "body_text": [
          [
            "School closed Friday",
            "The campus is closed on 16 Oct for a holiday.",
            "Sybeez Public School"
          ]
        ]
      }
    }
  ]
}
```
