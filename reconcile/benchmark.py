"""Synthetic DB/reconciliation benchmark. No real Worldline traffic or network throughput claim."""
import argparse
from datetime import datetime, timezone
import json
import platform
import tempfile
from time import perf_counter
import httpx
from sqlalchemy import insert
from .config import Settings
from .db import Database, conferences, orders, visitors, now_seconds
from .service import reconcile_batch, snapshot
from .worldline import WorldlineClient


def run(count):
    with tempfile.TemporaryDirectory(prefix="reconciliation-benchmark-") as temp:
        db = Database(f"sqlite:///{temp}/benchmark.db")
        db.init()
        settings = Settings()
        now = now_seconds()
        with db.transaction() as conn:
            conn.execute(insert(conferences).values(conference_id="bench", expo_id="bench", conference_name="Synthetic"))
            conn.execute(insert(orders), [{"order_id": f"order-{i}", "merchant_reference": f"ref-{i}",
                "merchant_id": settings.merchant_id, "expo_id": "bench", "order_status": "ORDER_PENDING",
                "amount_paise": 400_000, "currency": "INR", "gateway_date": "23-10-2026",
                "created_at": now-180, "next_check_at": now-60, "attempt_count": 0} for i in range(count)])
            conn.execute(insert(visitors), [{"visitor_id": f"visitor-{i}", "order_id": f"order-{i}",
                "conference_id": "bench", "expo_id": "bench", "visitor_data": {"name": "Synthetic"}} for i in range(count)])
        def handler(request):
            reference = json.loads(request.content)["transaction"]["identifier"]
            return httpx.Response(200, json={"merchantCode": settings.merchant_id,
                "merchantTransactionIdentifier": reference, "merchantTransactionRequestType": "O",
                "error": None, "paymentMethod": {"paymentTransaction": {"identifier": f"PG-{reference}",
                "amount": "4000.00", "statusCode": "0300"}}})
        with httpx.Client(transport=httpx.MockTransport(handler), trust_env=False) as client:
            gateway = WorldlineClient(settings.gateway_url, client)
            start = perf_counter()
            result = reconcile_batch(db, settings, gateway, limit=count, now=now)
            elapsed = perf_counter()-start
            repeated = reconcile_batch(db, settings, gateway, limit=count, now=now)
        report = snapshot(db)
        assert result == {"confirmed": count}
        assert repeated == {}
        assert report["passes"] == count
        assert report["confirmed_amount_paise"] == count * 400_000
        return {"measured_at_utc": datetime.now(timezone.utc).isoformat(),
            "python": platform.python_version(), "platform": platform.platform(),
            "database": "SQLite temporary file", "gateway_transport": "httpx.MockTransport; zero network latency",
            "synthetic_orders": count, "visitors_per_order": 1,
            "worker_concurrency": 1, "elapsed_seconds": round(elapsed, 3),
            "orders_per_second": round(count/elapsed, 2), "first_run": result,
            "second_run": repeated, "final": report,
            "limitation": "Local synthetic DB benchmark; excludes source latency, real gateway limits and PostgreSQL performance"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--orders", type=int, default=5000)
    args = parser.parse_args()
    if not 1 <= args.orders <= 100_000:
        parser.error("--orders must be between 1 and 100000")
    print(json.dumps(run(args.orders), indent=2))


if __name__ == "__main__":
    main()
