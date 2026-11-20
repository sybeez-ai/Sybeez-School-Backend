# WhatsApp AI assistant

This is the two-way assistant on the school WhatsApp number. Parents, students, teachers, school admins, finance, HR, payroll, and platform staff can message it. It answers from the school records that person is already allowed to see.

Outbound replies are **off** until you set `WHATSAPP_SENDING_ENABLED=true`. Until then the assistant still writes the reply into the transcript log, and nothing is sent to WhatsApp. There is no SMS path.

## What you type into Meta

You do this in a browser, in Meta for Developers. The app is already created.

1. Open [Meta for Developers](https://developers.facebook.com/apps/) and select the app **Sybeez School** (App ID `1590718079471217`).
2. In the left menu, open **WhatsApp** → **Configuration** (sometimes called **API setup**).
3. Confirm the number **+91 90921 10315**. Its phone number ID is `1462417083616289`. The WhatsApp Business Account ID is `1391395866490096`.
4. Under **Webhook**, click **Edit** (or **Configure**).
5. Callback URL. Paste this exactly:

   `https://api.school.sybeez.ai/api/v1/webhooks/whatsapp/meta`

   That host is the school API. `https://school.sybeez.ai` is the website people open in a browser. Meta has to call the API host above, on the path `/api/v1/webhooks/whatsapp/meta`. The same path is what this backend serves.
6. Verify token. Type a long random string you invent (for example 32 letters and numbers). You will put the **same** string in `WHATSAPP_VERIFY_TOKEN`. Meta does not generate this one. Click **Verify and save**. The API answers only when the two strings match.
7. On the same page, click **Manage** next to webhook fields and subscribe to **messages**. That is the field that delivers inbound texts and delivery receipts. Save.
8. Create a permanent token:
   - In [Business settings](https://business.facebook.com/settings), open **Users** → **System users**.
   - Create a system user (admin is fine) if you do not already have one.
   - **Add assets**: this app, and the WhatsApp account `1391395866490096`. Give the system user full control of the WhatsApp account.
   - **Generate token**. Select the app Sybeez School. Tick `whatsapp_business_messaging` and `whatsapp_business_management`. Choose **Never** expire if Meta offers it.
   - Copy the token once. You will not see it again. This becomes `WHATSAPP_ACCESS_TOKEN`. Do not put it in git, Slack, or a screenshot.
9. App secret: in the app, **App settings** → **Basic** → **App secret** → Show. This becomes `WHATSAPP_APP_SECRET`. The API uses it to check the `X-Hub-Signature-256` header. A webhook without a valid signature is rejected.

Session replies (someone just messaged the number) do not need a template. A message the school starts, outside that 24-hour window, must use a template Meta has already approved. The existing alert templates are listed in `apps/api/docs/WHATSAPP.md`. The assistant's own answers are session replies, so they do not need a new template.

## Secrets the API reads

Put these in the API and worker task environment, or in the JSON object stored in AWS Secrets Manager at `WHATSAPP_SECRETS_ARN` (or `ALERTS_SECRETS_ARN`). Environment variables win when both are set. Never commit the values.

| Name | What it is |
| --- | --- |
| `WHATSAPP_ACCESS_TOKEN` | Permanent system-user token from step 8 |
| `WHATSAPP_APP_SECRET` | App secret from step 9 |
| `WHATSAPP_VERIFY_TOKEN` | The verify string you invented in step 6 |
| `WHATSAPP_PHONE_NUMBER_ID` | `1462417083616289` |
| `WHATSAPP_BUSINESS_ACCOUNT_ID` | `1391395866490096` |
| `WHATSAPP_SENDING_ENABLED` | `false` until you deliberately turn sending on. Read **only** from the environment, not from the secrets JSON, so a secret update cannot turn sending on by accident |

`GET /api/v1/whatsapp-assistant/status` shows whether those values are present. It never returns the token or the secret. While any of the token, phone number id, app secret, or verify token is missing, status says they are missing by name.

Leave `WHATSAPP_SENDING_ENABLED` unset or `false` for the first deploy. Replies are generated and stored. Nothing is posted to `graph.facebook.com`.

## Turning a school on

Two switches have to be true before a reply leaves the server:

1. `WHATSAPP_SENDING_ENABLED=true` on the API (global, default false).
2. The school switch `assistant_enabled`, default false. A school admin (or a platform admin with `X-School-Id`) sets it with:

```
PUT /api/v1/whatsapp-assistant/settings
{"assistant_enabled": true, "leave_requests_enabled": false}
```

`leave_requests_enabled` lets a staff member file their **own** leave from WhatsApp. It is off. It cannot file leave for someone else, and it cannot move money.

There is also a per-minute rate limit (`WHATSAPP_ASSISTANT_RATE_LIMIT_PER_MINUTE`, default 30) and a per-school monthly cap on messages actually sent (`WHATSAPP_ASSISTANT_MONTHLY_CAP`, default 2000). If Redis is down, the assistant does not send.

Platform and school admins read the log at `GET /api/v1/whatsapp-assistant/transcripts`. Phone numbers in that log are masked. A school admin only sees their school. A platform admin can pass `school_id` or omit it to see every school, including numbers that matched nobody.

## How a person is recognised

The WhatsApp number is compared with the phone saved on the user, parent/guardian, student, teacher, and staff profile. `+91 90921 10315`, `9092110315`, and `919092110315` are the same number. A 10-digit number is treated as India. A number that already has another country code stays that country.

- One match: that person is signed in. No code is required.
- A parent with one linked child: that child is used.
- A parent with more than one child: the assistant asks which child, and will not open attendance, fees, or results until they answer.
- The same number on two profiles or two schools: the assistant asks which profile. It lists only that number's own profiles.
- No match: a short generic reply and no school data. The reply does not say whether a similar number exists.

To add a **different** number than the one on the profile, the signed-in person calls `POST /api/v1/whatsapp-assistant/link-code`. The app shows a 6-digit code for 10 minutes. They send `LINK 123456` from the new WhatsApp number. `DELETE /api/v1/whatsapp-assistant/link` removes those extra numbers. The profile phone keeps working.

`STOP` opts the number out of WhatsApp messages, same as the alert pipeline.

## What each role can see

The assistant calls the same permission set as the portal, then runs the query as that user and school so Postgres row-level security applies as well. Tool arguments cannot change the school.

- Parents: only their linked children.
- Students: only themselves, and lesson material uploaded for their class section. The persona is **AI teacher**. Doubts are answered from that material. If the teacher has not uploaded it, the assistant says so.
- Teachers: classes they are assigned to (or are class teacher of). Their own payslip and leave balance, not a colleague's.
- Finance: fees for their school, not another school, and not attendance unless that permission was granted.
- HR: own leave, and another staff member's leave only when they have HR access and name that person, still inside their school.
- Payroll: own payslip, and a named colleague's payslip only with payroll access, inside their school.
- School admin: their school.
- Platform staff: only what their portal grants. A platform login with no school membership is told that WhatsApp cannot open school records for that login.

Nothing in v1 changes data except an own leave request, and only when that school turned `leave_requests_enabled` on. No payments.

Conversation memory is the recent transcript for that phone and person. The wording goes through the AI service (`AI_SERVICE_URL`, header `X-Internal-Token` = `AI_SERVICE_TOKEN`). If the AI service is down, a small local planner still picks the tool, and the answer is built from the tool result. The model cannot call a tool that is not on the list.

`WHATSAPP_ASSISTANT_PROVIDER=local` skips the AI service and uses the local planner only. The default is `ai_service`.

## When the school is asleep

The budget setup stops the API, worker, AI service, and database after **15 minutes** with no user API traffic. ElastiCache, the load balancer, and NAT stay up. A visit to `https://school.sybeez.ai` shows a "waking up" page and starts the stack. That takes about 7–12 minutes.

A WhatsApp webhook does **not** wake it. The wake page only starts the stack for a browser visit, a `*.sybeez.ai` origin, or a request that carries `Authorization`. Meta's webhook has none of those, so it receives HTTP 503 and does not count as a wake. Meta retries a non-200 for a long time (on the order of a day, with longer gaps between tries). Those retries also look like the same anonymous call, so they do **not** start the school either. They only help if something else has already woken the stack before Meta gives up.

While the API is awake, this webhook returns 200 as soon as the message is on the Redis queue (`sybeez:jobs`). The slow reply happens in the worker, which calls back into the API. That is fast enough for Meta's timeout. It does not help while the tasks are scaled to zero, because nothing is listening.

Cheapest reliable option inside the $100/month budget: do **not** leave RDS and the three Fargate services running all night. NAT and the load balancer are already always on, and an always-on database plus three tasks can push the account over $100. Add one small Lambda (or teach the existing wake Lambda) that is the public webhook URL. It checks the signature, returns 200 to Meta immediately, invokes the existing idle-controller wake action, and forwards the body to `https://api.school.sybeez.ai/api/v1/webhooks/whatsapp/meta` once `https://api.school.sybeez.ai/ready` says the database is up. Lambda stays inside the free tier, so the extra cost is about nothing. This repository does not change that infrastructure.

Until that Lambda exists, WhatsApp messages sent while the school is asleep are not answered unless someone opens the site and Meta retries after the API is healthy.
