from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (BigInteger, CheckConstraint, Column, ForeignKey, Index,
                        Integer, JSON, MetaData, String, Table, UniqueConstraint,
                        create_engine, event)

metadata = MetaData()
orders = Table(
    "order_table", metadata,
    Column("order_id", String(64), primary_key=True),
    Column("merchant_reference", String(40), nullable=False, unique=True),
    Column("merchant_id", String(64), nullable=False),
    Column("expo_id", String(64), nullable=False),
    Column("order_status", String(32), nullable=False),
    Column("amount_paise", BigInteger, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("gateway_date", String(10), nullable=False),
    Column("gateway_payment_id", String(128), unique=True),
    Column("created_at", BigInteger, nullable=False),
    Column("confirmed_at", BigInteger),
    Column("next_check_at", BigInteger, nullable=False),
    Column("attempt_count", Integer, nullable=False, default=0),
    Column("lease_token", String(64)),
    Column("lease_until", BigInteger),
    CheckConstraint("amount_paise > 0"),
    CheckConstraint("currency = 'INR'"),
    CheckConstraint("order_status IN ('ORDER_PENDING','ORDER_PAID','ORDER_FAILED','REVIEW_REQUIRED')"),
)
Index("ix_due_orders", orders.c.order_status, orders.c.next_check_at, orders.c.created_at)
conferences = Table(
    "conference", metadata,
    Column("conference_id", String(64), primary_key=True),
    Column("expo_id", String(64), nullable=False),
    Column("conference_name", String(128), nullable=False),
)
visitors = Table(
    "visitor_table", metadata,
    Column("visitor_id", String(64), primary_key=True),
    Column("order_id", ForeignKey("order_table.order_id"), nullable=False),
    Column("conference_id", ForeignKey("conference.conference_id"), nullable=False),
    Column("expo_id", String(64), nullable=False),
    Column("visitor_data", JSON, nullable=False),
)
passes = Table(
    "visitor_pass_table", metadata,
    Column("pass_id", String(64), primary_key=True),
    Column("visitor_id", ForeignKey("visitor_table.visitor_id"), nullable=False),
    Column("conference_id", ForeignKey("conference.conference_id"), nullable=False),
    Column("pass_data", JSON, nullable=False),
    Column("created_at", BigInteger, nullable=False),
    UniqueConstraint("visitor_id", "conference_id", name="uq_visitor_conference_pass"),
)
checks = Table(
    "payment_checks", metadata,
    Column("check_id", String(64), primary_key=True),
    Column("order_id", ForeignKey("order_table.order_id"), nullable=False),
    Column("source", String(16), nullable=False),
    Column("outcome", String(64), nullable=False),
    Column("status_code", String(16)),
    Column("checked_at", BigInteger, nullable=False),
)
Index("ix_checks_order", checks.c.order_id, checks.c.checked_at)


def now_seconds():
    return int(datetime.now(timezone.utc).timestamp())


def new_id():
    return str(uuid4())


class Database:
    def __init__(self, url):
        self.engine = create_engine(url, pool_pre_ping=True)
        self.postgres = self.engine.dialect.name == "postgresql"
        if self.engine.dialect.name == "sqlite":
            @event.listens_for(self.engine, "connect")
            def configure(dbapi, _):
                dbapi.isolation_level = None
                dbapi.execute("PRAGMA foreign_keys=ON")
                dbapi.execute("PRAGMA busy_timeout=10000")
            @event.listens_for(self.engine, "begin")
            def begin(connection):
                # Local fallback serializes transactions; PG uses per-order locks.
                connection.exec_driver_sql("BEGIN IMMEDIATE")

    def init(self):
        metadata.create_all(self.engine)

    @contextmanager
    def transaction(self):
        with self.engine.begin() as conn:
            yield conn
