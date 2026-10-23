from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading
import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from reconcile.api import create_app
from reconcile.db import orders, visitors, passes, checks
from reconcile.service import (create_order, get_order, reconcile_batch, apply_evidence,
                               claim_one, snapshot)
from reconcile.worldline import (PaymentEvidence, InvalidEvidence, WorldlineClient,
                                offline_request, parse_offline_response, demo_callback,
                                verify_callback, paise)

NOW = 2_000_000_000


def seed(db, settings, people=1, age=180):
    return create_order(db, settings, 400_000, [{"name": f"Visitor {i}"} for i in range(people)], NOW-age)


def evidence(order, status="0300"):
    return PaymentEvidence(order["merchant_id"], order["merchant_reference"],
                           order["amount_paise"], "PG-" + order["merchant_reference"], status)


class Gateway:
    def verify(self, order):
        return evidence(order)


def response(order, status="0300"):
    return {"merchantCode": order["merchant_id"], "merchantTransactionIdentifier": order["merchant_reference"],
        "merchantTransactionRequestType": "O", "error": None,
        "paymentMethod": {"error": {"code": "", "desc": ""}, "paymentTransaction": {
            "amount": "4000.00", "identifier": "PG-" + order["merchant_reference"], "statusCode": status}}}


def test_missing_callback_recovered_and_repeated_run_empty(db, settings):
    order = seed(db, settings, people=2)
    assert snapshot(db)["passes"] == 0
    assert reconcile_batch(db, settings, Gateway(), now=NOW) == {"confirmed": 1}
    assert reconcile_batch(db, settings, Gateway(), now=NOW) == {}
    assert get_order(db, order["order_id"])["order_status"] == "ORDER_PAID"
    assert snapshot(db)["passes"] == 2


def test_young_pending_not_selected(db, settings):
    seed(db, settings, age=30)
    assert reconcile_batch(db, settings, Gateway(), now=NOW) == {}


def test_crash_rolls_back_status_passes_and_audit(db, settings):
    order = seed(db, settings)
    def crash():
        raise RuntimeError("simulated process crash")
    with pytest.raises(RuntimeError):
        apply_evidence(db, settings, order["order_id"], evidence(order), "cron", NOW, fault=crash)
    assert get_order(db, order["order_id"])["order_status"] == "ORDER_PENDING"
    assert snapshot(db)["passes"] == 0
    with db.transaction() as conn:
        assert conn.execute(select(func.count()).select_from(checks)).scalar_one() == 0
    assert reconcile_batch(db, settings, Gateway(), now=NOW) == {"confirmed": 1}


@pytest.mark.parametrize("change", [{"amount_paise": 1}, {"reference": "wrong"}, {"merchant": "wrong"}])
def test_mismatches_require_review_without_pass(db, settings, change):
    order = seed(db, settings)
    assert apply_evidence(db, settings, order["order_id"], replace(evidence(order), **change), "cron", NOW) == "evidence_mismatch"
    assert snapshot(db)["passes"] == 0
    assert get_order(db, order["order_id"])["order_status"] == "REVIEW_REQUIRED"


@pytest.mark.parametrize("status, expected", [("0398", "pending"), ("0396", "pending"), ("new", "unknown_status"), ("0399", "gateway_failed")])
def test_non_success_never_creates_pass(db, settings, status, expected):
    order = seed(db, settings)
    assert apply_evidence(db, settings, order["order_id"], evidence(order, status), "cron", NOW) == expected
    assert snapshot(db)["passes"] == 0


def test_one_gateway_error_does_not_stop_other_order(db, settings):
    a, b = seed(db, settings), seed(db, settings)
    class Flaky:
        def verify(self, order):
            if order["order_id"] == a["order_id"]:
                raise httpx.ReadTimeout("simulated")
            return evidence(order)
    outcomes = reconcile_batch(db, settings, Flaky(), now=NOW)
    assert outcomes == {"gateway_unavailable": 1, "confirmed": 1} or outcomes == {"confirmed": 1, "gateway_unavailable": 1}
    assert get_order(db, a["order_id"])["next_check_at"] > NOW
    assert get_order(db, b["order_id"])["order_status"] == "ORDER_PAID"


def test_retry_exhaustion_is_review_not_failed(db, settings):
    settings = replace(settings, max_attempts=1)
    order = seed(db, settings)
    apply_evidence(db, settings, order["order_id"], evidence(order, "0398"), "cron", NOW)
    assert get_order(db, order["order_id"])["order_status"] == "REVIEW_REQUIRED"


def test_expired_lease_recovers_after_worker_crash(db, settings):
    order = seed(db, settings)
    assert claim_one(db, settings, NOW)["order_id"] == order["order_id"]
    assert claim_one(db, settings, NOW + 1) is None
    assert reconcile_batch(db, settings, Gateway(), now=NOW + settings.lease_seconds + 1) == {"confirmed": 1}


def test_two_workers_claim_different_orders(db, settings):
    seed(db, settings)
    seed(db, settings)
    with ThreadPoolExecutor(2) as pool:
        claimed = list(pool.map(lambda _: claim_one(db, settings, NOW), range(2)))
    assert len({c["order_id"] for c in claimed}) == 2


def test_concurrent_callback_and_cron_confirm_once(db, settings):
    order = seed(db, settings)
    barrier = threading.Barrier(2)
    def confirm(source):
        barrier.wait(timeout=5)
        return apply_evidence(db, settings, order["order_id"], evidence(order), source, NOW)
    with ThreadPoolExecutor(2) as pool:
        outcomes = list(pool.map(confirm, ["callback", "cron"]))
    assert sorted(outcomes) == ["already_confirmed", "confirmed"]
    assert snapshot(db)["passes"] == 1


def test_offline_contract_and_http_adapter(db, settings):
    order = seed(db, settings)
    payload = offline_request(order)
    assert payload["transaction"]["requestType"] == "O"
    assert payload["transaction"]["identifier"] == order["merchant_reference"]
    def handler(request):
        import json
        assert json.loads(request.content) == payload
        return httpx.Response(200, json=response(order))
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert WorldlineClient(settings.gateway_url, client).verify(order) == evidence(order)


@pytest.mark.parametrize("amount", ["NaN", "Infinity", "-1", "0", "1.001", None])
def test_invalid_amounts(amount):
    with pytest.raises(InvalidEvidence):
        paise(amount)


def test_malformed_response_rejected():
    with pytest.raises(InvalidEvidence):
        parse_offline_response({"paymentMethod": None})


def test_live_endpoint_disabled():
    with pytest.raises(ValueError):
        WorldlineClient("https://securepg.paynimo.com/api/paynimoV2.req")


def test_callback_hash_and_tampering(db, settings):
    order = seed(db, settings)
    msg = demo_callback(order["merchant_reference"], order["amount_paise"], settings.callback_salt)
    assert verify_callback(msg, settings.callback_salt) == order["merchant_reference"]
    with pytest.raises(InvalidEvidence):
        verify_callback(msg.replace("4000.00", "1.00"), settings.callback_salt)
    with pytest.raises(InvalidEvidence):
        verify_callback("0300|success", settings.callback_salt)


def test_http_registration_callback_duplicate_and_report(db, settings):
    with TestClient(create_app(settings, db, Gateway())) as client:
        result = client.post("/orders", json={"amount_paise": 400_000, "visitors": [{"name": "Demo"}]})
        assert result.status_code == 201
        order = result.json()
        msg = demo_callback(order["merchant_reference"], order["amount_paise"], settings.callback_salt)
        assert client.post("/callbacks/worldline", data={"msg": "invalid"}).status_code == 400
        assert client.post("/callbacks/worldline", data={"msg": msg}).json() == {"outcome": "confirmed"}
        assert client.post("/callbacks/worldline", data={"msg": msg}).json() == {"outcome": "already_confirmed"}
        assert len(client.get(f"/orders/{order['order_id']}").json()["passes"]) == 1
        assert client.get("/reports/reconciliation").json()["passes"] == 1


def test_payment_id_cannot_confirm_two_orders(db, settings):
    first, second = seed(db, settings), seed(db, settings)
    first_evidence = evidence(first)
    apply_evidence(db, settings, first["order_id"], first_evidence, "cron", NOW)
    second_evidence = replace(evidence(second), payment_id=first_evidence.payment_id)
    assert apply_evidence(db, settings, second["order_id"], second_evidence, "cron", NOW) == "payment_id_conflict"
    assert get_order(db, second["order_id"])["order_status"] == "REVIEW_REQUIRED"
    assert snapshot(db)["passes"] == 1


def test_concurrent_registration_bootstrap(db, settings):
    with ThreadPoolExecutor(2) as pool:
        result = list(pool.map(lambda _: seed(db, settings), range(2)))
    assert len({r["order_id"] for r in result}) == 2


def test_callback_verification_timeout_leaves_pending(db, settings):
    order = seed(db, settings)
    class Offline:
        def verify(self, _):
            raise httpx.ConnectTimeout("simulated")
    with TestClient(create_app(settings, db, Offline())) as client:
        msg = demo_callback(order["merchant_reference"], order["amount_paise"], settings.callback_salt)
        assert client.post("/callbacks/worldline", data={"msg": msg}).status_code == 503
    assert get_order(db, order["order_id"])["order_status"] == "ORDER_PENDING"
    assert snapshot(db)["passes"] == 0
