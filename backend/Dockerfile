FROM python:3.12-slim
WORKDIR /app
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY server.py catalog.py test_server.py test_catalog.py ./
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65532:65532
CMD ["uvicorn", "server:production_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
