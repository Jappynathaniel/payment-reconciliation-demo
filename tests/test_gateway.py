from fastapi.testclient import TestClient
from reconcile.gateway import create_gateway


def test_independent_gateway_contract(tmp_path):
    with TestClient(create_gateway(f"sqlite:///{tmp_path / 'gateway.db'}")) as client:
        assert client.post("/demo/payments", json={"reference": "ref-1", "amount_paise": 12345,
            "gateway_date": "23-10-2026"}).status_code == 200
        payload = {"merchant": {"identifier": "DEMO_MERCHANT"}, "transaction": {
            "requestType": "O", "deviceIdentifier": "S", "currency": "INR",
            "identifier": "ref-1", "dateTime": "23-10-2026"}}
        response = client.post("/api/paynimoV2.req", json=payload)
        assert response.json()["paymentMethod"]["paymentTransaction"]["amount"] == "123.45"
        payload["transaction"]["requestType"] = "TSI"
        assert client.post("/api/paynimoV2.req", json=payload).status_code == 400
