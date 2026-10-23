from contextlib import asynccontextmanager
from typing import Annotated
from fastapi import FastAPI, Form, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from .config import Settings
from .db import Database, orders, visitors, passes
from .service import create_order, get_order, apply_evidence, snapshot
from .worldline import WorldlineClient, verify_callback, InvalidEvidence
import httpx


class Person(BaseModel):
    name: str = Field(min_length=1, max_length=128)


class Registration(BaseModel):
    amount_paise: int = Field(gt=0, le=100_000_000)
    visitors: list[Person] = Field(min_length=1, max_length=20)


def create_app(settings=None, db=None, gateway=None):
    settings = settings or Settings.from_env()
    db = db or Database(settings.database_url)
    gateway = gateway or WorldlineClient(settings.gateway_url)

    @asynccontextmanager
    async def lifespan(_):
        db.init()
        yield

    app = FastAPI(title="Registration payment recovery — local demonstration", lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"status": "ok", "mode": "local-simulator-only"}

    @app.post("/orders", status_code=201)
    def register(body: Registration):
        return create_order(db, settings, body.amount_paise, [p.model_dump() for p in body.visitors])

    @app.get("/orders/{order_id}")
    def read(order_id: str):
        order = get_order(db, order_id)
        if not order:
            raise HTTPException(404, "Unknown order")
        with db.transaction() as conn:
            order["passes"] = list(conn.execute(select(passes.c.pass_id, passes.c.visitor_id).join(
                visitors, passes.c.visitor_id == visitors.c.visitor_id).where(
                visitors.c.order_id == order_id)).mappings().all())
        return order

    @app.post("/callbacks/worldline")
    def callback(msg: Annotated[str, Form(max_length=8192)]):
        try:
            reference = verify_callback(msg, settings.callback_salt)
        except InvalidEvidence:
            raise HTTPException(400, "Invalid callback")
        with db.transaction() as conn:
            row = conn.execute(select(orders).where(orders.c.merchant_reference == reference)).mappings().first()
            order = dict(row) if row else None
        if not order:
            raise HTTPException(404, "Unknown merchant reference")
        try:
            # Signed callback is a notification; server verification is authoritative.
            evidence = gateway.verify(order)
            return {"outcome": apply_evidence(db, settings, order["order_id"], evidence, "callback")}
        except (httpx.HTTPError, InvalidEvidence):
            raise HTTPException(503, "Verification unavailable; reconciliation will retry")

    @app.get("/reports/reconciliation")
    def report():
        return snapshot(db)

    return app


app = create_app()
