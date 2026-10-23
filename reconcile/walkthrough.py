"""Self-contained real-HTTP walkthrough using temporary, synthetic databases."""
from dataclasses import replace
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import httpx
from .cli import demo
from .config import Settings
from .db import Database


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main():
    with tempfile.TemporaryDirectory(prefix="reconciliation-demo-") as temp:
        api_port, gateway_port = free_port(), free_port()
        while gateway_port == api_port:
            gateway_port = free_port()
        settings = replace(Settings(), database_url=f"sqlite:///{temp}/orders.db",
            gateway_url=f"http://127.0.0.1:{gateway_port}/api/paynimoV2.req")
        env = {**os.environ, "DATABASE_URL": settings.database_url,
            "GATEWAY_URL": settings.gateway_url, "MERCHANT_ID": settings.merchant_id,
            "CALLBACK_SALT": settings.callback_salt, "MIN_AGE_SECONDS": str(settings.min_age_seconds),
            "SIMULATOR_DATABASE_URL": f"sqlite:///{temp}/gateway.db"}
        processes = []
        try:
            for module, port in [("reconcile.gateway:app", gateway_port), ("reconcile.api:app", api_port)]:
                processes.append(subprocess.Popen([sys.executable, "-m", "uvicorn", module,
                    "--host", "127.0.0.1", "--port", str(port), "--log-level", "error"], env=env))
            with httpx.Client(timeout=1, trust_env=False) as client:
                for port, path in [(api_port, "/health"), (gateway_port, "/openapi.json")]:
                    for attempt in range(100):
                        if any(p.poll() is not None for p in processes):
                            raise RuntimeError("Demo service exited during startup")
                        try:
                            client.get(f"http://127.0.0.1:{port}{path}").raise_for_status()
                            break
                        except httpx.HTTPError:
                            if attempt == 99:
                                raise
                            time.sleep(.1)
            db = Database(settings.database_url)
            result = demo(db, settings, f"http://127.0.0.1:{api_port}/callbacks/worldline")
            assert result["first_run"] == {"confirmed": 1}
            assert result["second_run"] == {}
            assert result["delayed_callback"] == {"outcome": "already_confirmed"}
            assert result["after"]["passes"] == 1
            result["transport"] = "actual HTTP to two local uvicorn processes"
            print(json.dumps(result, indent=2))
        finally:
            for process in processes:
                process.terminate()
            for process in processes:
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
