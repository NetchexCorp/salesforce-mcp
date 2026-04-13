FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
COPY salesforce_mcp/ salesforce_mcp/

RUN pip install --no-cache-dir .

EXPOSE 8765

CMD ["python", "-m", "salesforce_mcp", "streamable-http"]
