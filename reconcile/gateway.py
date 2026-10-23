"""Independent local gateway simulator; never contacts Worldline."""
from contextlib import asynccontextmanager
from decimal import Decimal
import os
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Column, String, BigInteger, MetaData, Table, insert, select, update
from .db import Database

ledger_meta = MetaData()
ledger = Table("simulated_gateway_ledger", ledger_meta,
    Column("reference", String(40), primary_key=True),
    Column("merchant", String(64), nullable=False),
    Column("amount_paise", BigInteger, nullable=False),
    Column("gateway_date", String(10), nullable=False),
    Column("status", String(16), nullable=False),
)


class SimulatedPayment(BaseModel):
    reference: str = Field(min_length=1, max_length=40)
    merchant: str = "DEMO_MERCHANT"
    amount_paise: int = Field(gt=0)
    gateway_date: str
    status: str = "0300"


def create_gateway(url=None):
    db = Database(url or os.getenv("SIMULATOR_DATABASE_URL", "sqlite:///gateway.db"))
    @asynccontextmanager
    async def lifespan(_):
        ledger_meta.create_all(db.engine)
        yield
    app = FastAPI(title="Local Worldline-contract simulator (not Worldline)", lifespan=lifespan)

    @app.post("/demo/payments")
    def record(body: SimulatedPayment):
        values = body.model_dump()
        with db.transaction() as conn:
            existing = conn.execute(select(ledger).where(ledger.c.reference == body.reference)).first()
            if existing:
                conn.execute(update(ledger).where(ledger.c.reference == body.reference).values(**values))
            else:
                conn.execute(insert(ledger).values(**values))
        return {"recorded": True, "callback_delivered": False}

    @app.post("/api/paynimoV2.req")
    def verify(body: dict):
        try:
            txn = body["transaction"]
            if txn["requestType"] != "O" or txn["deviceIdentifier"] != "S" or txn["currency"] != "INR":
                raise ValueError()
            with db.transaction() as conn:
                payment = conn.execute(select(ledger).where(ledger.c.reference == txn["identifier"])).mappings().first()
            if not payment or payment["merchant"] != body["merchant"]["identifier"] or payment["gateway_date"] != txn["dateTime"]:
                raise HTTPException(404, "No matching simulated payment")
            return {
                "merchantCode": payment["merchant"], "merchantTransactionIdentifier": payment["reference"],
                "merchantTransactionRequestType": "O", "error": None,
                "paymentMethod": {"error": {"code": "", "desc": ""}, "paymentTransaction": {
                    "amount": f"{Decimal(payment['amount_paise']) / 100:.2f}",
                    "identifier": "PG-" + payment["reference"], "statusCode": payment["status"],
                    "statusMessage": "SUCCESS" if payment["status"] == "0300" else "SIMULATED",
                }},
            }
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, "Invalid offline verification request")
    return app


app = create_gateway()
