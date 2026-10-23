# Design and tradeoffs

## What is established versus reconstructed

| Item | Basis |
|---|---|
| PostgreSQL; order, visitor, pass and conference tables | Earlier user-supplied SQL references |
| Visitor stored before payment; pass generated after confirmation | Author's description |
| Worldline India; missing notifications; cron checks aged pending orders | Author's description |
| 5,000 registrations; ₹2 crore+ payment value | Author-reported production impact, not repository measurement |
| Separate merchant reference, atomic transaction, unique constraints, claims and audit table | New reconstruction choices |
| Multiple visitors/order; one pass/visitor/conference | Explicit demo assumption |
| Callback hash shape and offline enquiry fields | Public Checkout docs; see integration notes |

## Choices

| Choice | Benefit | Cost / change trigger |
|---|---|---|
| Cron wakes a bounded worker | Small operational footprint; restartable process | Adds polling delay; batch capacity must exceed incoming unresolved work |
| PostgreSQL as work queue | Pending status and claims share a consistency boundary | Adds database traffic; measure before introducing a broker |
| Claim one order at a time | Lease starts immediately before that order's network request | More claim transactions than claiming whole batches |
| Row lock + `SKIP LOCKED` for PG claims | Overlapping workers claim different orders | Requires PostgreSQL concurrency tests |
| Lease expiry | Work survives worker death | Work may be attempted again; confirmation must be idempotent |
| Shared confirmation | Callback and cron enforce identical rules | Requires careful transaction boundaries |
| Status/pass/audit in one transaction | Crash cannot leave a new paid order without its pass | Only applies to same-database effects |
| Unique visitor/conference pass | Database rejects duplicate allocations | Depends on the chosen pass cardinality |
| Exact integer paise | Predictable matching without float errors | INR-only demo; currency scales need expansion for multi-currency |
| SQLite fallback | Easy local walkthrough without Docker | Serializes transactions; not a PostgreSQL performance substitute |

## Confirmation race

A callback and cron can discover the same successful payment concurrently. Each queries the gateway without a DB lock. Both then enter the shared confirmation function. The first locks and confirms the order; the second sees `ORDER_PAID` and returns the existing result. The unique pass constraint supplies another guard.

A unique gateway payment ID prevents one payment from confirming two different orders. A cross-order race is caught by that unique constraint and retried/reviewed rather than creating two paid registrations.

## Retry and review policy

Defaults are demo choices, not Worldline recommendations:

- Initial eligibility: 120 seconds after order creation.
- Batch limit: 50 orders; one worker processes them sequentially.
- HTTP timeout: 5 seconds, including a 2-second connection timeout.
- Lease: 120 seconds per claimed order.
- Retry delay: exponential, capped at one hour.
- After ten unsuccessful checks: review required; no assumption of payment failure.
- Mismatched identity/amount: review required immediately.
- No automatic refunds or duplicate charges.

A 50-order batch every minute can handle at most 50 new checks/minute even when requests are instant, and fewer when the worker is slow. This is not a claim to recover 5,000 cases in a minute. Track oldest pending age, backlog and gateway limits; increase batch capacity or add bounded workers only after measurement.

Callback attempts are audited; rejected hashes return 400 without logging payloads. Expected gateway/schema failures are recorded per order so the batch continues. Unexpected software/database failures stop the process and leave leases recoverable, rather than being silently swallowed.

## Limits and next production steps

The API intentionally runs locally without user authentication. No real financial/visitor records are included. Production would require access control, merchant-specific verification, schema migrations, database backups, alerting for stale/failed cron runs, request pacing and provider-limit validation. No claim of distributed exactly-once delivery is made.

The report currently summarizes statuses, pass counts and confirmed amounts. It counts order amounts once even if an order contains multiple visitors. Delivery side effects would need a transactional outbox and an independently retryable sender; this version creates pass records only.
