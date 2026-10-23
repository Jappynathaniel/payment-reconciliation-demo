#!/bin/sh
set -eu
cd /absolute/path/payment-reconciliation-demo
set -a
. /absolute/path/protected-demo.env
set +a
exec .venv/bin/python -m reconcile.cli reconcile --limit 50
