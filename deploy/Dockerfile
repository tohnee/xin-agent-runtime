FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml .
COPY src/ src/
COPY conftest.py .
COPY tests/ tests/

RUN pip install --no-cache-dir -e ".[dev]" pyyaml

EXPOSE 8900

ENV XRUNTIME_SERVER_PORT=8900

CMD ["python", "-m", "xruntime._server"]
