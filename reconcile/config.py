from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///registrations.db"
    gateway_url: str = "http://127.0.0.1:8001/api/paynimoV2.req"
    merchant_id: str = "DEMO_MERCHANT"
    callback_salt: str = "local-demo-salt-never-use-for-real-payments"
    min_age_seconds: int = 120
    lease_seconds: int = 120
    max_attempts: int = 10

    @classmethod
    def from_env(cls):
        return cls(
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            gateway_url=os.getenv("GATEWAY_URL", cls.gateway_url),
            merchant_id=os.getenv("MERCHANT_ID", cls.merchant_id),
            callback_salt=os.getenv("CALLBACK_SALT", cls.callback_salt),
            min_age_seconds=int(os.getenv("MIN_AGE_SECONDS", "120")),
        )
