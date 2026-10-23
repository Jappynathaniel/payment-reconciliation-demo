"""Worldline India Checkout Offline Verification (O), not mandate TSI.

Only a loopback simulator is enabled in this reconstruction. The public docs
are insufficient to certify a particular merchant's production configuration.
"""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
from urllib.parse import urlparse
import httpx


class InvalidEvidence(ValueError):
    pass


@dataclass(frozen=True)
class PaymentEvidence:
    merchant: str
    reference: str
    amount_paise: int
    payment_id: str
    status: str


def paise(value):
    try:
        amount = Decimal(str(value))
        if not amount.is_finite() or amount <= 0 or amount * 100 != (amount * 100).to_integral_value():
            raise InvalidEvidence("Invalid INR amount")
        return int(amount * 100)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise InvalidEvidence("Invalid INR amount") from exc


def offline_request(order):
    # Persist this date at payment initiation in the gateway's expected timezone.
    datetime.strptime(order["gateway_date"], "%d-%m-%Y")
    return {
        "merchant": {"identifier": order["merchant_id"]},
        "transaction": {
            "deviceIdentifier": "S", "currency": order["currency"],
            "identifier": order["merchant_reference"],
            "dateTime": order["gateway_date"], "requestType": "O",
        },
    }


def parse_offline_response(body):
    try:
        if body.get("error"):
            raise InvalidEvidence("Gateway error response")
        if body.get("merchantTransactionRequestType") != "O":
            raise InvalidEvidence("Unexpected verification response type")
        method = body["paymentMethod"]
        if any(method.get("error", {}).get(k) for k in ("code", "desc")):
            raise InvalidEvidence("Gateway payment error")
        txn = method["paymentTransaction"]
        evidence = PaymentEvidence(
            str(body["merchantCode"]), str(body["merchantTransactionIdentifier"]),
            paise(txn["amount"]), str(txn["identifier"]), str(txn["statusCode"]),
        )
        if not evidence.merchant or not evidence.reference or evidence.payment_id in ("", "None"):
            raise InvalidEvidence("Incomplete verification response")
        return evidence
    except (KeyError, TypeError, AttributeError) as exc:
        raise InvalidEvidence("Malformed verification response") from exc


class WorldlineClient:
    def __init__(self, endpoint, client=None):
        url = urlparse(endpoint)
        if url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "gateway"}:
            raise ValueError("Demo permits only the local gateway simulator; live integration is disabled")
        self.endpoint = endpoint
        self.client = client

    def verify(self, order):
        payload = offline_request(order)
        def request(client):
            response = client.post(self.endpoint, json=payload)
            response.raise_for_status()
            try:
                return parse_offline_response(response.json())
            except ValueError as exc:
                raise InvalidEvidence("Invalid verification response") from exc
        if self.client is not None:
            return request(self.client)
        # No implicit retry: the database records and schedules each attempt.
        with httpx.Client(timeout=httpx.Timeout(5, connect=2), follow_redirects=False, trust_env=False) as client:
            return request(client)


def verify_callback(msg, salt):
    fields = msg.split("|")
    if len(fields) != 16:
        raise InvalidEvidence("Expected 16 callback fields")
    expected = hashlib.sha512(("|".join(fields[:-1]) + "|" + salt).encode()).hexdigest()
    if not hmac.compare_digest(expected, fields[-1].lower()):
        raise InvalidEvidence("Invalid callback hash")
    if not fields[3]:
        raise InvalidEvidence("Missing merchant reference")
    return fields[3]


def demo_callback(reference, amount_paise, salt, status="0300"):
    # A synthetic payload for the documented Checkout returnUrl shape.
    fields = [status, "success", "", reference, "DEMO", "PG-" + reference,
              f"{Decimal(amount_paise) / 100:.2f}", "", "", "", "", "", "", "", ""]
    signature = hashlib.sha512(("|".join(fields) + "|" + salt).encode()).hexdigest()
    return "|".join([*fields, signature])
