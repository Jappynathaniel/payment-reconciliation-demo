# Validation record

Run date: 23 October 2026. Python 3.12.14 in the build environment.

## Executed

- 30 tests passed against file-backed SQLite, including actual concurrent threads for work claiming, confirmation and registration creation.
- Real HTTP walkthrough against two local Uvicorn processes: missed callback recovered, one pass created, rerun empty, delayed callback returned `already_confirmed`.
- Synthetic 5,000-order reconciliation run: all confirmed, exactly 5,000 passes, unchanged second run. Elapsed time approximately 35.1 seconds; see the machine-readable result for details.
- PostgreSQL schema was compiled from the SQLAlchemy models.
- GitHub Actions run [39936210072](https://github.com/Jappynathaniel/payment-reconciliation-demo/actions/runs/39936210072) passed both SQLite and PostgreSQL test stages, including transaction and concurrency tests, on 23 October 2026.

The benchmark used SQLite and `httpx.MockTransport`; it excluded network latency and real provider limits. It is evidence of the tested implementation path, not production capacity or Worldline performance.

## Not validated

- Docker Compose build/start: Docker was unavailable in the build environment.
- Real Worldline sandbox/production transactions: deliberately disabled; no merchant account used.

## Test scope

Tests cover atomic rollback between status/pass writes, matching identifiers and amount, repeated callbacks, overlapping workers, stale leases, retry exhaustion, unknown statuses, independent per-order errors, callback hashes, HTTP request/response parsing, and multi-visitor pass creation.

This reconstruction does not independently verify the author's historical production impact. Its synthetic workload and the original incident are separate evidence categories.
