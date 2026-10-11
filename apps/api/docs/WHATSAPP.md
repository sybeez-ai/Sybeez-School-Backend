# WhatsApp alerts and SMS fallback

Sybeez sends business-initiated WhatsApp messages with Meta-approved UTILITY templates. SMS through MSG91 is the fallback when WhatsApp fails or the number is not on WhatsApp, and it is the direct channel when the user opted into SMS only. Free text is used only inside the 24-hour customer service window after the parent or staff member messages the school number.

Platform default templates are seeded for locales `en`, `ta`, and `hi`. A school can override a template. Both channels stay off until a school admin enables them.

Silver already has a dedicated number, a WhatsApp Business account on the mobile app, a Meta developer account, and a created app. Meta Business verification is still pending, so there is no access token and no app secret yet. The API and the worker start cleanly in that state. `GET /api/v1/alerts/status` returns `whatsapp_status: "WhatsApp not configured"` and the sender stays on the mock provider. Nothing is posted to Meta or MSG91, and a missing secret does not crash the process.

## Where credentials are read

Every credential is read from the process environment, or from one JSON secret in AWS Secrets Manager referenced by `ALERTS_SECRETS_ARN` or `WHATSAPP_SECRETS_ARN`. Environment variables win when both are set. The repository does not contain a live token.

| Credential | Environment variable |
| --- | --- |
| Permanent token | `WHATSAPP_ACCESS_TOKEN` (`WHATSAPP_CLOUD_ACCESS_TOKEN` is still accepted) |
| Cloud API phone number id | `WHATSAPP_PHONE_NUMBER_ID` (`WHATSAPP_CLOUD_PHONE_NUMBER_ID` is still accepted) |
| WhatsApp Business Account id | `WHATSAPP_BUSINESS_ACCOUNT_ID` |
| Meta app secret | `WHATSAPP_APP_SECRET` |
| Webhook verify token | `WHATSAPP_VERIFY_TOKEN` |
| MSG91 auth key | `MSG91_AUTH_KEY` |
| MSG91 sender id | `MSG91_SENDER_ID` |
| MSG91 default template id | `MSG91_TEMPLATE_ID` |
| MSG91 webhook token | `MSG91_WEBHOOK_TOKEN` |

`whatsapp_status` stays `WhatsApp not configured` until the access token, phone number id, business account id, app secret, and verify token are all present. `sms_status` stays `SMS not configured` until `MSG91_AUTH_KEY` and `MSG91_SENDER_ID` are present. Setting `WHATSAPP_PROVIDER=meta_cloud` or `SMS_PROVIDER=msg91` before those values exist still sends through mock. After the values exist, set:

```
WHATSAPP_PROVIDER=meta_cloud
SMS_PROVIDER=msg91
```

`gupshup` and `twilio` are stub adapters and do not send.

## Business verification

The legal name on the Meta Business account is **Sybis Technology Private Limited**. Meta compares that name with the public website. Put the same legal name, spelled the same way, on the website footer, the contact page, and the privacy page. A marketing name alone on those pages is a common reason verification stays pending. Verification has to finish before Meta issues a permanent system-user token or shows a production app secret for this WhatsApp Business Account.

## Cloud API number

The dedicated number is on the WhatsApp Business mobile app today. The Cloud API cannot send from a number that stays only on that app. Before `WHATSAPP_PHONE_NUMBER_ID` exists, do one of these:

- Migrate the number off the WhatsApp Business app and onto the Cloud API. The handset loses the WhatsApp Business app for that number.
- Complete Meta coexistence onboarding so the same number stays on the WhatsApp Business app and on the Cloud API.

After either step, copy the Cloud API phone number id into `WHATSAPP_PHONE_NUMBER_ID` and the WhatsApp Business Account id into `WHATSAPP_BUSINESS_ACCOUNT_ID`.

## SMS is a separate MSG91 account

WhatsApp credentials do not send SMS. SMS needs its own MSG91 account. On the DLT portal, register the principal entity (Sybis Technology Private Limited), a six-character sender id, and each SMS text in the template section below. Copy each returned DLT template id onto `sms_dlt_template_id`. `{#var#}` is one variable, in the order listed on that template.

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
| MSG91 auth key | Auth key for the Flow API, from the separate MSG91 account | `MSG91_AUTH_KEY` |
| MSG91 sender id | Six-character DLT-approved header | `MSG91_SENDER_ID` |
| DLT registration | Entity, sender id, and each SMS text registered. Copy the template id onto the row. | `sms_dlt_template_id` on each template |
| MSG91 webhook token | Shared secret the MSG91 callback sends in `X-Msg91-Token` | `MSG91_WEBHOOK_TOKEN` |

After the secrets are present, a school admin enables the channels with `PUT /api/v1/alerts/settings`. Quiet hours default to 21:00–07:00 Asia/Kolkata. Student absent and student late bypass quiet hours. Other alerts wait until 07:00. Users still need `whatsapp_enabled` or `sms_enabled` on their notification preference. `STOP` opts that phone out.

Webhook callback URLs:

- `GET` and `POST /api/v1/webhooks/whatsapp/meta`
- `POST /api/v1/webhooks/sms/msg91`

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
- Category: Utility. Name and language must match the JSON (`sybeez_{event}_{locale}`, language `en`, `hi`, or `ta`). Paste the BODY text. Enter the sample row from `example.body_text`. Submit each locale.
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
| GET | `/api/v1/alerts/status` | `whatsapp_status` is `WhatsApp not configured` or `configured`. Also `sms_status`, effective providers, and the env names still blank. |
| GET | `/api/v1/alerts/settings` | Per-school enable flags, quiet hours, timezone, default locale, plus the same provider status fields. |
| PUT | `/api/v1/alerts/settings` | Update any of `whatsapp_enabled`, `sms_enabled`, `quiet_hours_start`, `quiet_hours_end` (`HH:MM`), `timezone`, `default_locale`. |
| GET | `/api/v1/alerts/templates` | Effective templates. School rows override platform defaults. Query: `event_key`, `locale`. |
| POST | `/api/v1/alerts/templates` | Create a school-specific template. |
| POST | `/api/v1/alerts/templates/submit` | Submit active templates to the WABA `message_templates` API. `dry_run=true` returns the JSON. Missing token or business account id returns status `WhatsApp not configured`. |
| PUT | `/api/v1/alerts/templates/{id}` | Update a school template. Editing a platform default copies it onto the school first. |

Delivery item fields: `id`, `school_id`, `user_id`, `channel`, `notification_type`, `event_key`, `to`, `to_masked`, `title`, `body`, `provider`, `provider_message_id`, `status`, `attempts`, `last_error`, `error_code`, `locale`, `template_name`, `scheduled_for`, `sent_at`, `delivered_at`, `read_at`, `failed_at`, `created_at`.

Status values: `PENDING`, `SCHEDULED`, `SENT`, `DELIVERED`, `READ`, `FAILED`.

## Behaviour

- Parents receive student absent, student late, weekly attendance, fee due, fee overdue, and payment receipt. Payroll staff receive pay-run approved. The employee receives payslip published, LOP update, and staff leave approved. Student leave approved goes to parents. Announcements go to the announcement audience.
- WhatsApp is sent first when the school and the user allow it. SMS is queued only when WhatsApp is not available, or after a WhatsApp send fails (including Meta errors such as 131026, not a WhatsApp user).
- Duplicate sends with the same school, channel, and dedupe key are skipped unless the earlier row failed.
- Inbound WhatsApp messages are stored. `STOP`, `STOPALL`, `UNSUBSCRIBE`, `CANCEL`, `END`, `QUIT`, and `OPT OUT` record an opt-out and turn the preference off.
- Phone numbers are masked in logs and in `alert_audit_logs`. New tables use forced row-level security on `school_id`. Platform default templates (`school_id` null) are readable by the school. Global opt-outs are readable so a STOP is honored.
- The worker consumes the Redis list `sybeez:jobs`. `JOBS_BACKEND=sqs` with `JOBS_SQS_QUEUE_URL` sends and polls SQS instead. Provider rate limits default to 60 WhatsApp and 100 SMS messages per minute (`WHATSAPP_RATE_LIMIT_PER_MINUTE`, `SMS_RATE_LIMIT_PER_MINUTE`).
- A job tagged `meta_cloud` or `msg91` is sent as mock when the matching credentials are blank. The worker logs `WhatsApp not configured` or `SMS not configured` and does not call Graph or MSG91.
- Scheduled quiet-hour rows are released by `POST /internal/v1/alerts/release-due`. Weekly summaries are `POST /internal/v1/attendance/{school_id}/digests/weekly/run`.

## Meta template JSON

Submit each object to the WhatsApp message template API. Category is UTILITY. Do not change the name or the `{{n}}` order.

### student_absent

A parent is told a child was marked absent.

Locale `en`. Variables, in order: student_name, date, school_name.

DLT text:

```
{#var#} was absent on {#var#}. - {#var#}
```

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

DLT text:

```
{#var#} {#var#} को अनुपस्थित रहे। - {#var#}
```

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

DLT text:

```
{#var#} {#var#} அன்று பள்ளிக்கு வரவில்லை. - {#var#}
```

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

DLT text:

```
{#var#} arrived late on {#var#}. - {#var#}
```

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

DLT text:

```
{#var#} {#var#} को देर से पहुंचे। - {#var#}
```

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

DLT text:

```
{#var#} {#var#} அன்று தாமதமாக வந்தார். - {#var#}
```

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

DLT text:

```
Weekly attendance for {#var#} ({#var#}): present {#var#}, absent {#var#}, late {#var#}. - {#var#}
```

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

DLT text:

```
{#var#} की साप्ताहिक उपस्थिति ({#var#}): उपस्थित {#var#}, अनुपस्थित {#var#}, विलंब {#var#}। - {#var#}
```

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

DLT text:

```
{#var#} வார வருகை ({#var#}): வந்த நாட்கள் {#var#}, வராத நாட்கள் {#var#}, தாமத நாட்கள் {#var#}. - {#var#}
```

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

DLT text:

```
{#var#} has a fee of {#var#} due on {#var#}. Invoice {#var#}. - {#var#}
```

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

DLT text:

```
{#var#} की फीस {#var#} {#var#} को देय है। चालान {#var#}। - {#var#}
```

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

DLT text:

```
{#var#} அவர்களின் கட்டணம் {#var#} {#var#} அன்று செலுத்த வேண்டும். இன்வாய்ஸ் {#var#}. - {#var#}
```

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

DLT text:

```
{#var#} has a fee of {#var#} overdue since {#var#}. Invoice {#var#}. - {#var#}
```

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

DLT text:

```
{#var#} की फीस {#var#} {#var#} से अतिदेय है। चालान {#var#}। - {#var#}
```

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

DLT text:

```
{#var#} அவர்களின் கட்டணம் {#var#} {#var#} முதல் நிலுவையில் உள்ளது. இன்வாய்ஸ் {#var#}. - {#var#}
```

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

DLT text:

```
{#var#}: payment of {#var#} received. Receipt {#var#} on {#var#}. - {#var#}
```

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

DLT text:

```
{#var#} के लिए {#var#} का भुगतान मिला। रसीद {#var#}, दिनांक {#var#}। - {#var#}
```

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

DLT text:

```
{#var#} அவர்களுக்கான {#var#} செலுத்தப்பட்டது. ரசீது {#var#}, தேதி {#var#}. - {#var#}
```

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

DLT text:

```
{#var#}, your payslip for {#var#} is published. Net pay INR {#var#}. - {#var#}
```

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

DLT text:

```
{#var#}, {#var#} की वेतन पर्ची जारी हो गई है। शुद्ध वेतन INR {#var#}। - {#var#}
```

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

DLT text:

```
{#var#}, {#var#} சம்பள சீட்டு வெளியிடப்பட்டது. நிகர ஊதியம் INR {#var#}. - {#var#}
```

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

DLT text:

```
Pay run {#var#} is approved for {#var#} employees. - {#var#}
```

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

DLT text:

```
वेतन रन {#var#} {#var#} कर्मचारियों के लिए स्वीकृत है। - {#var#}
```

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

DLT text:

```
{#var#} சம்பள ஓட்டம் {#var#} ஊழியர்களுக்கு அங்கீகரிக்கப்பட்டது. - {#var#}
```

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

DLT text:

```
{#var#}, loss of pay for {#var#} is {#var#} day(s). - {#var#}
```

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

DLT text:

```
{#var#}, {#var#} के लिए वेतन कटौती {#var#} दिन है। - {#var#}
```

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

DLT text:

```
{#var#}, {#var#} காலத்திற்கான ஊதிய இழப்பு {#var#} நாட்கள். - {#var#}
```

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

DLT text:

```
{#var#}, your {#var#} leave from {#var#} to {#var#} is approved. - {#var#}
```

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

DLT text:

```
{#var#}, {#var#} से {#var#} तक की {#var#} छुट्टी स्वीकृत है। - {#var#}
```

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

DLT text:

```
{#var#}, {#var#} முதல் {#var#} வரையிலான {#var#} விடுப்பு அங்கீகரிக்கப்பட்டது. - {#var#}
```

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

DLT text:

```
{#var#}: {#var#} - {#var#}
```

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

DLT text:

```
{#var#}: {#var#} - {#var#}
```

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

DLT text:

```
{#var#}: {#var#} - {#var#}
```

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
