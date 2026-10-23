# Three-minute interview walkthrough

## Explain the problem

“A customer could pay successfully through Worldline India while our registration database remained pending because the payment notification was missed. The visitor details existed, but the pass had not been generated.”

## Explain the recovery

“A cron job checked older pending orders against the gateway. Successful verification triggered registration confirmation and pass creation using the connected order, visitor and pass records.”

Reported impact: roughly 5,000 registrations associated with ₹2 crore+ in successful payments. These are the author's recalled production figures. Explain how they were estimated; do not describe them as audited repository measurements or recovered lost funds.

## Show this reconstruction

Run `python -m reconcile.walkthrough`. Point to the pending state, first recovery, empty rerun and harmless delayed callback. Then show `service.py`: the gateway call is outside the transaction; order confirmation and pass writes commit together.

## Explain one failure test

Open `test_crash_rolls_back_status_passes_and_audit`. It interrupts execution after setting the status but before creating the pass. The transaction rolls back and reconciliation can recover on the next run.

## Explain one tradeoff

“Cron plus database claims is enough for a bounded periodic workflow. More components would need a demonstrated requirement. PostgreSQL claims use per-row locking; the simple local SQLite walkthrough serializes transactions.”

## Distinguish new work

“This repository is a reconstruction. The original workflow and reported impact come from my experience. These explicit transaction, locking and audit choices are how I made the demonstration testable; I would not claim every safeguard existed in the original code.”

## Relate to the Data Engineer role

The useful evidence is cross-system collection, identity matching, validation, SQL transformations/state updates, scheduling, error isolation and reproducible recovery. This is an operational reconciliation workflow rather than a full analytical warehouse.
