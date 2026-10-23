import argparse
import json
import time
import httpx
from sqlalchemy import select
from .config import Settings
from .db import Database, orders, now_seconds
from .service import create_order, reconcile_batch, snapshot
from .worldline import WorldlineClient, demo_callback


def demo(db, settings, callback_url=None):
    gateway = WorldlineClient(settings.gateway_url)
    now = now_seconds()
    order = create_order(db, settings, 400_000, [{"name": "Synthetic Visitor"}],
                         now=now - settings.min_age_seconds - 1)
    simulator_url = settings.gateway_url.removesuffix("/api/paynimoV2.req")
    with httpx.Client(timeout=5, trust_env=False) as client:
        response = client.post(simulator_url + "/demo/payments", json={
            "reference": order["merchant_reference"], "merchant": order["merchant_id"],
            "amount_paise": order["amount_paise"], "gateway_date": order["gateway_date"], "status": "0300",
        })
        response.raise_for_status()
    with db.transaction() as conn:
        before = conn.execute(select(orders.c.order_status).where(orders.c.order_id == order["order_id"])).scalar_one()
    first = reconcile_batch(db, settings, gateway)
    second = reconcile_batch(db, settings, gateway)
    delayed = None
    if callback_url:
        with httpx.Client(timeout=10, trust_env=False) as client:
            response = client.post(callback_url, data={"msg": demo_callback(
                order["merchant_reference"], order["amount_paise"], settings.callback_salt)})
            response.raise_for_status()
            delayed = response.json()
    return {"scenario": "synthetic missing callback", "order_id": order["order_id"],
        "before": before, "first_run": first, "second_run": second,
        "delayed_callback": delayed, "after": snapshot(db)}


def main():
    parser = argparse.ArgumentParser(description="Registration payment reconciliation demo")
    parser.add_argument("command", choices=["init-db", "reconcile", "report", "demo", "schedule"])
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--callback-url")
    args = parser.parse_args()
    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")
    settings = Settings.from_env()
    db = Database(settings.database_url)
    db.init()
    if args.command == "init-db":
        result = {"initialized": True}
    elif args.command == "report":
        result = snapshot(db)
    elif args.command == "demo":
        result = demo(db, settings, args.callback_url)
    elif args.command == "schedule":
        # Compose convenience only. The documented deployment uses external cron.
        while True:
            print(json.dumps(reconcile_batch(db, settings, WorldlineClient(settings.gateway_url), args.limit)), flush=True)
            time.sleep(60)
    else:
        result = reconcile_batch(db, settings, WorldlineClient(settings.gateway_url), args.limit)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
