# When payment succeeded but registration stayed pending

*A case study in payment-status reconciliation with Worldline India, PostgreSQL and cron.*

**Jasper Nathaniel**

During my SDE internship at Showbay, I worked on a registration flow where a customer could complete payment and still remain unconfirmed in our system. Their details were saved, but the pass was never generated.

The payment had succeeded at Worldline India. The missing step was the notification that allowed our backend to update the order. My work on the recovery flow used a cron job to check older pending orders against the gateway and complete registration when payment success was confirmed.

The recovery covered approximately **5,000 registrations associated with ₹2 crore+ in successful payments**. These are my estimates of the production impact. The money was already paid; the work recovered registration confirmation and pass generation.

I later reconstructed this workflow in a public repository so the failure and recovery could be demonstrated. The original flow is the basis of the case study. The explicit locking, transaction boundaries, work claims and tests described below are choices in that reconstruction.

## Where the flow broke

Before payment, the application saved an order and its visitor records. The order contained a payment reference and a local `order_status`. When payment succeeded, the normal notification path updated that status and triggered pass creation through the linked database records.

The relevant relationships were straightforward:

| Table | What it connected |
|---|---|
| `order_table` | The registration order and its payment state |
| `visitor_table` | Visitor details, linked to the order through `order_id` |
| `visitor_pass_table` | A pass, linked to the visitor through `visitor_id` and to the conference through `conference_id` |

When the payment notification was missed, Worldline and our database disagreed:

| Record | State |
|---|---|
| Gateway payment | Successful |
| Local order | Pending |
| Visitor details | Present |
| Pass | Missing |

The failure we needed to handle was a missing notification. I do not have evidence establishing why each notification was missed, so I would not attribute all cases to a particular network or gateway fault.

The important distinction was that an unconfirmed local order did not establish a failed payment. It meant our application had not established the final result. Asking the customer to pay again would not resolve that uncertainty safely.

## Giving pending orders an independent recovery path

The recovery job selected orders that were still pending after a waiting period. That gave the normal notification path time to complete before an additional gateway enquiry was made.

For each selected order, the job checked the payment result using its stored reference. A confirmed success allowed the application to update the order and generate the pass for the visitor who was already registered. A pending result had to remain unresolved until another check.

There was no limited-seat allocation in this registration flow. A reservation TTL would not have addressed the problem. The age of an order was useful for deciding when to investigate it; elapsed time alone could not tell us whether payment had failed.

Cron was a reasonable fit for this bounded, periodic task. A separate broker and distributed task system would have added components without changing the central requirement: identify unresolved orders and make their state agree with verified payment results. The tradeoff was polling delay. Recovery happened on a later run rather than immediately.

For the reconstruction, I kept that structure and made its processing rules explicit. The worker claims a due order, queries the simulated gateway, validates the result, and calls the same confirmation function used by the callback handler.

## Matching the verification request to the integration

While rebuilding the flow, I checked the Worldline India Checkout documentation rather than assuming that any transaction-status API would apply. The relevant operation is **Offline Verification, request type `O`**, which uses the merchant reference. The public documentation also covers other verification operations, including mandate flows, so choosing the correct contract matters. [1]

The reconstruction stores the internal `order_id` separately from the merchant-facing reference. It checks that a verification response matches the expected merchant, reference and amount before allowing confirmation. Amounts are represented as integer paise. The demonstration is INR-only; it does not claim to validate a currency field that is absent from the documented response example.

The callback path checks the documented hash before using the notification's reference, then independently queries payment status. The public Checkout document describes a return-URL callback; it does not establish the delivery guarantees of the original server-webhook configuration. I therefore do not claim a particular Worldline retry policy.

Live gateway access is disabled in the repository. Its simulator follows the relevant request and response shape, but a real deployment would still need merchant-specific access requirements, enquiry limits and date-lookup behaviour confirmed with Worldline.

## Keeping confirmation and pass creation together

A successful gateway enquiry only solves half the problem. The application still has to apply that result consistently across its own tables.

Separate SQL queries are not inherently a problem. The failure window appears when their changes can commit independently. If `order_status` becomes paid and the process crashes before the pass insert, the application has created another inconsistent registration.

In the reconstruction, the shared confirmation function performs the related database writes in one transaction:

```text
Query the gateway before opening the transaction.

BEGIN
    Lock the order row.
    If already paid, return the existing confirmation.
    Check payment evidence against the order.
    Mark the order paid.
    Create any missing passes for its linked visitors.
    Record the confirmation outcome.
COMMIT
```

If the process fails between the status update and pass creation, the transaction rolls back. The order remains recoverable.

The gateway request stays outside this transaction. A slow external service should not hold an order lock while our database waits for a network response.

Both callback and reconciliation use this function because they are reporting the same business event. Maintaining two implementations would make it easier for validation or pass-generation behaviour to diverge.

## Making repeated work safe

A delayed callback may arrive while cron is recovering the same order. The reconstruction handles that race with a row lock. One operation confirms the order; the other then sees the paid state and returns without creating another pass.

A unique constraint on `(visitor_id, conference_id)` also prevents duplicate pass allocation under the demonstration's cardinality rule. A unique gateway payment identifier prevents the same payment from being attached to two paid orders.

That is the scope of idempotency here: repeated confirmation attempts do not repeat the registration's database effects. It does not mean the network delivers notifications exactly once, or that an email would be sent exactly once. This version creates pass records; delivery would need its own retryable design.

I also made cron overlap and worker crashes explicit. PostgreSQL workers claim orders with `FOR UPDATE SKIP LOCKED`, save a temporary lease and release the transaction before calling the gateway. A crashed worker's lease eventually expires, allowing another run to retry. Error rescheduling checks the lease token so an older worker cannot overwrite a newer claim's retry schedule.

Claims reduce duplicate work. The confirmation transaction and constraints protect the business result when work is repeated anyway.

## Handling uncertainty without inventing a result

The worker treats outcomes differently:

| Outcome | Reconstruction behaviour |
|---|---|
| Verified matching success | Confirm the order and create its passes |
| Documented failure | Record the failed state |
| Pending or unknown status | Schedule another check; create no pass |
| Timeout or malformed response | Record the failed check and retry later |
| Merchant, reference or amount mismatch | Flag for review |
| Repeatedly unresolved | Escalate for review rather than declaring failure from age |

An expected error on one order does not stop the rest of the batch. Unexpected software or database failures are allowed to fail the run, leaving claimed work recoverable after lease expiry.

The demonstration's waiting period, retry intervals and batch size are configurable engineering choices, not Worldline recommendations. In deployment, I would size them using the unresolved-order arrival rate, oldest pending age, observed gateway latency and permitted enquiry frequency.

## What I tested

The most useful tests exercise the points where the systems can disagree:

- A payment succeeds while its callback is omitted: reconciliation confirms registration.
- The callback arrives after recovery: the existing pass is preserved.
- Callback and cron confirm concurrently: one confirmation and one pass result.
- Execution fails after the status write: status, pass and audit changes roll back together.
- A worker dies after claiming an order: a later run recovers it after lease expiry.
- Gateway evidence has the wrong amount or reference: no pass is created.
- One enquiry times out: other eligible orders still get processed.

The repository contains **30 tests**, with successful GitHub Actions runs against both SQLite and PostgreSQL. A separate walkthrough uses actual HTTP between two local services to demonstrate recovery and the delayed-callback case. [2]

I also ran 5,000 synthetic orders through the worker. It created exactly 5,000 passes, and a second run made no further changes. That run took about 35.1 seconds using SQLite and an in-process mocked HTTP transport. It validates that workload under those conditions; it does not measure Worldline throughput, production latency or PostgreSQL performance.

The production impact and synthetic benchmark are separate: the former describes the original recovery work, while the latter is a repeatable check of the reconstruction.

The main lesson I took from this work is that payment success and completed registration are separate facts. The notification path normally connects them. Reconciliation provides a way to recover when that connection is missed, and the database transaction makes the recovery safe to repeat.

## Code and references

- [Repository and runnable walkthrough](https://github.com/Jappynathaniel/payment-reconciliation-demo)
- [1] [Worldline India Checkout documentation](https://www.paynimo.com/paynimocheckout/docs/): verification and callback contract used for the reconstruction.
- [2] [Passing SQLite and PostgreSQL CI run](https://github.com/Jappynathaniel/payment-reconciliation-demo/actions/runs/39936210072).
- [Stripe: Designing robust and predictable APIs with idempotency](https://stripe.com/blog/idempotency): useful background on ambiguous network outcomes and safe retries.
- [Shopify: Building Resilient GraphQL APIs Using Idempotency](https://shopify.engineering/building-resilient-graphql-apis-using-idempotency): useful comparison of recovery approaches and concurrent requests.
- [Shopify: 10 Tips for Building Resilient Payment Systems](https://shopify.engineering/building-resilient-payment-systems): context for explicit timeouts, testing and operating payment integrations.

The engineering articles informed the explanation and review of this reconstruction. They are not evidence about the original Showbay implementation.
