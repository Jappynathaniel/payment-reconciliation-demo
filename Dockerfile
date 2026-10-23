FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY reconcile ./reconcile
COPY pyproject.toml ./
RUN pip install --no-deps .
CMD ["uvicorn", "reconcile.api:app", "--host", "0.0.0.0", "--port", "8000"]
