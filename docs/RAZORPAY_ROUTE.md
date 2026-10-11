# Razorpay Route

Sybeez keeps one platform Razorpay account. Each school is a Route linked account. When a parent pays a fee online, Razorpay transfers the school's share to that linked account and keeps the platform fee on the Sybeez account.

Do this in **test mode** first. Do not point a school at live keys until Razorpay has activated that linked account.

## Prerequisites

1. Route is enabled on the Sybeez platform Razorpay account. Razorpay has to approve Route for the platform before `POST /v2/accounts` with `"type": "route"` succeeds.
2. Platform API keys are available to the API process, and only to the API process:
   - `RAZORPAY_KEY_ID`
   - `RAZORPAY_KEY_SECRET`
   - `RAZORPAY_WEBHOOK_SECRET`
3. The same three names can live in Secrets Manager instead of the environment. Set `RAZORPAY_SECRETS_ARN` to a JSON secret, or put the keys in the alerts secret (`ALERTS_SECRETS_ARN` / `WHATSAPP_SECRETS_ARN`). Environment variables win when both are set.
4. Bank account numbers are encrypted at rest. Set one of:
   - `FEE_KMS_KEY_ID` (AWS KMS). The API task role needs `kms:Encrypt` and `kms:Decrypt`.
   - `FEE_FIELD_KEY` or `RAZORPAY_FIELD_KEY` (any secret string; it is stretched into a Fernet key).
   Production refuses to store a settlement account when neither is configured. The database stores the ciphertext and the last four digits. PAN, GST, and the full account number are not written to the audit log.
5. The webhook endpoint below is registered on the **platform** Razorpay account (test mode first, then live). Use the webhook secret from that endpoint as `RAZORPAY_WEBHOOK_SECRET`.

## Webhook

URL:

`https://api.school.sybeez.ai/api/v1/payments/webhook/razorpay`

Subscribe to these events:

| Event | What Sybeez does |
| --- | --- |
| `payment.captured` | Marks the fee payment paid, posts the student ledger, and issues the receipt. Idempotent on `x-razorpay-event-id`. |
| `payment.failed` | Marks a still-pending payment failed. Does not issue a receipt. |
| `order.paid` | Same credit rule as `payment.captured` when the payment is captured. |
| `transfer.processed` | Records the Route transfer as processed for that payment. |
| `transfer.failed` | Records the failure reason. The student receipt stays issued; finance sees it on the failed-transfer report. |
| `settlement.processed` | Attaches the settlement id when the amount matches one open transfer or the open transfers for that linked account. |
| `refund.processed` | Confirms the refund and marks the school's transfer reversed. The student ledger is debited once. |
| `refund.failed` | Marks the refund failed when it is not already successful. |
| `account.activated` | Linked account is activated. |
| `account.needs_clarification` | Linked account needs clarification. |
| `account.under_review` | Linked account is under review. |
| `account.suspended` | Linked account is suspended. Online payments stop unless a super admin allows platform settlement. |
| `product.route.under_review` | Route product is under review. |
| `product.route.needs_clarification` | Route product needs clarification. |
| `product.route.activated` | Route product is activated. Online payments can transfer to the school. |
| `product.route.suspended` | Route product is suspended. |

Signature: HMAC-SHA256 of the raw body with `RAZORPAY_WEBHOOK_SECRET`, hex digest, header `X-Razorpay-Signature`. A bad signature is rejected and nothing is credited.

## Money movement

Platform fee for a school is `platform_fee_percent` (default 0) plus optional `platform_fee_fixed` rupees. Only a platform super admin can change those, or the settlement hold flags, or `allow_platform_settlement`.

The fee is calculated in paise with half-up rounding:

`fee_paise = round_half_up(total_paise * percent / 100) + fixed_paise`

`transfer_paise = total_paise - fee_paise`

The order is created only when the school's Route status is `activated` and a linked account id is stored:

```json
{
  "amount": 10000,
  "currency": "INR",
  "transfers": [
    {
      "account": "acc_linked",
      "amount": 9750,
      "currency": "INR",
      "notes": {"invoice_id": "...", "school_id": "...", "payment_number": "..."},
      "on_hold": false
    }
  ]
}
```

`on_hold` follows `settlement_on_hold`. A future `settlement_hold_until` also holds the transfer and is sent as `on_hold_until`.

Schools that already collect on the Sybeez Razorpay account stay blocked after this migration until a super admin sets `allow_platform_settlement` or the school's Route account reaches `activated`.

If the linked account is not activated, online payment creation returns HTTP 422:

> Online payments are blocked until this school's Razorpay Route account is activated. The payment was not settled to the Sybeez platform account.

Nothing is sent to Razorpay in that case. There is no silent settlement onto the Sybeez account. A super admin can set `allow_platform_settlement` for a school; only then is an order created with no `transfers` array.

Refunds of a Route payment send `reverse_all: true` so Razorpay reverses the school's share. The student ledger gets one refund debit. Replaying `refund.processed` does not debit it again.

The student ledger still shows the gross amount the parent paid. Platform fee and transfer status live on `fee_route_transfers` and in the reconciliation report.

## Onboarding API

School admin, accountant, or a staff member granted `finance.manage` can call:

| Method | Path |
| --- | --- |
| GET | `/api/v1/fees/route` and `/api/v1/finance/route` |
| POST | `/api/v1/fees/route/account` — `POST /v2/accounts` with `type: route` |
| POST | `/api/v1/fees/route/stakeholder` — stakeholder on that account |
| POST | `/api/v1/fees/route/settlement` — request the Route product, then PATCH settlement bank details |
| POST | `/api/v1/fees/route/refresh` — poll account and product activation status |

The same paths exist under `/api/v1/finance/route/...`.

Status values stored on `fee_settings.route_status`: `not_started`, `created`, `needs_clarification`, `under_review`, `activated`, `suspended`.

Each step writes a `fee_audit_logs` row (`ROUTE_ACCOUNT_CREATED`, `ROUTE_STAKEHOLDER_ADDED`, `ROUTE_PRODUCT_REQUESTED`, `ROUTE_SETTLEMENT_UPDATED`, `ROUTE_STATUS_CHANGED`). Bank numbers in those rows are the last four digits only.

Super admin:

| Method | Path |
| --- | --- |
| GET | `/api/v1/platform/route/accounts` |
| PATCH | `/api/v1/platform/schools/{school_id}/route` |

The patch body may set `platform_fee_percent`, `platform_fee_fixed`, `allow_platform_settlement`, `settlement_on_hold`, and `settlement_hold_until`. School settings cannot change those fields.

## Reports

| Path | Contents |
| --- | --- |
| `GET /api/v1/fees/route/reports/transfers` | Transfers for the current school. Optional `status`. |
| `GET /api/v1/fees/route/reports/platform-fees` | Gross collected, platform fees, amount transferred, failed count. |
| `GET /api/v1/fees/route/reports/failed-transfers` | Transfers Razorpay failed. |
| `GET /api/v1/fees/route/reports/reconciliation` | Each Razorpay payment against its transfer: matched, pending, failed, missing, or explicit platform settlement. |
| `GET /api/v1/fees/online-payments/reconciliation` | Existing online-payment list, plus transfer status, transfer amount, platform fee, and settlement id. |

The finance portal serves the same Route reports under `/api/v1/finance/route/reports/...`.

## Schema

Migration `0054_razorpay_route` revises `0053`. `0051` and `0052` stay reserved for the hrm-integration branch. `fee_route_transfers` has forced row-level security: `school_id` must match `app.school_id`, and `app.can_see('finance', ...)` must allow the role. A school admin session cannot read or write another school's transfers.

## Frontend still to build

This repository is the API. The school web app needs two screens:

### `/finance` settings — Route onboarding

- Show `route_status`, linked account id, masked account (`bank_account_last4`), IFSC, beneficiary, and legal name from `GET /api/v1/finance/route`.
- Step 1: business form (legal name, contact, email, phone, address, business type, optional PAN and GST) posted to `POST /api/v1/finance/route/account`.
- Step 2: stakeholder form posted to `POST /api/v1/finance/route/stakeholder`.
- Step 3: account number, IFSC, beneficiary name, and an explicit terms checkbox posted to `POST /api/v1/finance/route/settlement`. Do not display the full account number after save; show the last four digits.
- A refresh button calls `POST /api/v1/finance/route/refresh` and renders `created`, `needs_clarification`, `under_review`, `activated`, or `suspended`.
- Show platform fee percent, fixed fee, and settlement hold as read-only. Those controls belong on the platform super-admin school screen (`PATCH /api/v1/platform/schools/{id}/route`), not on the school settings page.
- Disable the settlement form until terms are accepted.

### Pay page

The pay page (`/pay/{order_id}`, opened from the payment link) should:

- Keep using Razorpay Checkout with the order id and `public_key` returned by `POST /api/v1/payments/orders`. Transfers are already attached to the order on the server. Checkout.js does not need a transfer object.
- When order creation returns 422 with the blocked-route message, show that message and do not open Checkout. Do not offer a fallback that charges the Sybeez platform account.
- When the order response includes `platform_fee_amount` and `transfer_amount`, the parent still pays `amount` (the full fee). The split is informational for the school; the parent is not asked to pay the platform fee on top.
- After Checkout, keep the existing `POST /api/v1/payments/verify` call. The webhook remains the source that issues the receipt if the browser never returns.
