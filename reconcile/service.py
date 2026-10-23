from datetime import datetime
from zoneinfo import ZoneInfo
from sqlalchemy import select, update, insert, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
import httpx

from .db import orders, visitors, passes, conferences, checks, new_id, now_seconds
from .worldline import InvalidEvidence


def get_order(db, order_id):
    with db.transaction() as conn:
        row = conn.execute(select(orders).where(orders.c.order_id == order_id)).mappings().first()
        return dict(row) if row else None


def create_order(db, settings, amount_paise, people, now=None):
    now = now_seconds() if now is None else now
    order_id, reference = new_id(), new_id().replace("-", "")
    with db.transaction() as conn:
        conference_id = "demo-conference"
        # Demo bootstrap is safe when concurrent registrations start together.
        dialect_insert = pg_insert if db.postgres else sqlite_insert
        conn.execute(dialect_insert(conferences).values(conference_id=conference_id,
            expo_id="demo-expo", conference_name="Demo Conference").on_conflict_do_nothing(
                index_elements=["conference_id"]))
        conn.execute(insert(orders).values(
            order_id=order_id, merchant_reference=reference, merchant_id=settings.merchant_id,
            expo_id="demo-expo", order_status="ORDER_PENDING", amount_paise=amount_paise,
            currency="INR", gateway_date=datetime.fromtimestamp(now, ZoneInfo("Asia/Kolkata")).strftime("%d-%m-%Y"),
            created_at=now, next_check_at=now + settings.min_age_seconds, attempt_count=0,
        ))
        for person in people:
            conn.execute(insert(visitors).values(visitor_id=new_id(), order_id=order_id,
                conference_id=conference_id, expo_id="demo-expo", visitor_data=person))
    return get_order(db, order_id)


def audit(conn, order_id, source, outcome, now, status=None):
    conn.execute(insert(checks).values(check_id=new_id(), order_id=order_id,
        source=source, outcome=outcome, checked_at=now, status_code=status))


def apply_evidence(db, settings, order_id, evidence, source, now=None, fault=None):
    """One confirmation path shared by callback and cron; no network I/O here."""
    now = now_seconds() if now is None else now
    with db.transaction() as conn:
        query = select(orders).where(orders.c.order_id == order_id)
        if db.postgres:
            query = query.with_for_update()
        order = conn.execute(query).mappings().one()
        if order["order_status"] == "ORDER_PAID":
            audit(conn, order_id, source, "already_confirmed", now, evidence.status)
            return "already_confirmed"
        if order["order_status"] not in {"ORDER_PENDING", "REVIEW_REQUIRED"}:
            audit(conn, order_id, source, "terminal_no_change", now, evidence.status)
            return "terminal_no_change"
        matches = (evidence.reference == order["merchant_reference"]
            and evidence.merchant == order["merchant_id"]
            and evidence.amount_paise == order["amount_paise"])
        if not matches:
            outcome, state = "evidence_mismatch", "REVIEW_REQUIRED"
        elif evidence.status == "0300":
            previous = conn.execute(select(orders.c.order_id).where(
                orders.c.gateway_payment_id == evidence.payment_id,
                orders.c.order_id != order_id)).first()
            if previous:
                outcome, state = "payment_id_conflict", "REVIEW_REQUIRED"
            else:
                conn.execute(update(orders).where(orders.c.order_id == order_id).values(
                    order_status="ORDER_PAID", gateway_payment_id=evidence.payment_id,
                    confirmed_at=now, lease_token=None, lease_until=None))
                if fault:
                    fault()  # Test crash between status and pass writes: must roll back.
                people = conn.execute(select(visitors).where(visitors.c.order_id == order_id)).mappings().all()
                if not people:
                    raise InvalidEvidence("Order has no visitors")
                for person in people:
                    exists = conn.execute(select(passes.c.pass_id).where(
                        passes.c.visitor_id == person["visitor_id"],
                        passes.c.conference_id == person["conference_id"])).first()
                    if not exists:
                        conn.execute(insert(passes).values(pass_id=new_id(), visitor_id=person["visitor_id"],
                            conference_id=person["conference_id"], pass_data={"registration": "confirmed"},
                            created_at=now))
                audit(conn, order_id, source, "confirmed", now, evidence.status)
                return "confirmed"
        elif evidence.status == "0399":
            outcome, state = "gateway_failed", "ORDER_FAILED"
        else:
            outcome = "pending" if evidence.status in {"0398", "0396"} else "unknown_status"
            state = "ORDER_PENDING"
        attempts = order["attempt_count"] + 1
        if state == "ORDER_PENDING" and attempts >= settings.max_attempts:
            state, outcome = "REVIEW_REQUIRED", "retry_exhausted"
        conn.execute(update(orders).where(orders.c.order_id == order_id).values(
            order_status=state, attempt_count=attempts,
            next_check_at=now + min(3600, 30 * 2 ** min(attempts, 7)),
            lease_token=None, lease_until=None))
        audit(conn, order_id, source, outcome, now, evidence.status)
        return outcome


def claim_one(db, settings, now):
    with db.transaction() as conn:
        query = select(orders).where(
            orders.c.order_status == "ORDER_PENDING",
            orders.c.created_at <= now - settings.min_age_seconds,
            orders.c.next_check_at <= now,
            (orders.c.lease_until.is_(None) | (orders.c.lease_until <= now)),
        ).order_by(orders.c.next_check_at, orders.c.order_id).limit(1)
        if db.postgres:
            query = query.with_for_update(skip_locked=True)
        row = conn.execute(query).mappings().first()
        if not row:
            return None
        claimed = dict(row)
        claimed["lease_token"] = new_id()
        conn.execute(update(orders).where(orders.c.order_id == row["order_id"]).values(
            lease_token=claimed["lease_token"], lease_until=now + settings.lease_seconds))
        return claimed


def record_error(db, settings, order, category, now):
    # Token guard prevents an old worker from rescheduling a newer claim.
    with db.transaction() as conn:
        attempts = order["attempt_count"] + 1
        result = conn.execute(update(orders).where(
            orders.c.order_id == order["order_id"],
            orders.c.order_status == "ORDER_PENDING",
            orders.c.lease_token == order["lease_token"],
        ).values(attempt_count=attempts, next_check_at=now + min(3600, 30 * 2 ** min(attempts, 7)),
            lease_token=None, lease_until=None,
            order_status="REVIEW_REQUIRED" if attempts >= settings.max_attempts else "ORDER_PENDING"))
        if result.rowcount:
            audit(conn, order["order_id"], "cron", category, now)


def reconcile_batch(db, settings, gateway, limit=50, now=None):
    results = {}
    for _ in range(limit):
        tick = now_seconds() if now is None else now
        order = claim_one(db, settings, tick)
        if not order:
            break
        try:
            evidence = gateway.verify(order)  # No DB transaction held during HTTP call.
            result = apply_evidence(db, settings, order["order_id"], evidence, "cron", tick)
        except (httpx.HTTPError, InvalidEvidence, IntegrityError) as exc:
            result = "invalid_evidence" if isinstance(exc, InvalidEvidence) else (
                "database_conflict" if isinstance(exc, IntegrityError) else "gateway_unavailable")
            record_error(db, settings, order, result, tick)
        results[result] = results.get(result, 0) + 1
    return results


def snapshot(db):
    with db.transaction() as conn:
        states = dict(conn.execute(select(orders.c.order_status, func.count()).group_by(orders.c.order_status)).all())
        total_passes = conn.execute(select(func.count()).select_from(passes)).scalar_one()
        total_paid = conn.execute(select(func.coalesce(func.sum(orders.c.amount_paise), 0)).where(
            orders.c.order_status == "ORDER_PAID")).scalar_one()
        return {"orders_by_status": states, "passes": total_passes, "confirmed_amount_paise": total_paid}
