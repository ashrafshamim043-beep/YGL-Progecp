# Y.G.L Office System backend -- development/staging image.
# Production image hardening (multi-stage, non-root user, distroless base)
# is a Sub-phase 8.6 (Security/DR/Production Launch) task -- this Dockerfile
# is intentionally simple, dev/staging-appropriate scaffolding for Phase 8.1.
FROM python:3.12-slim

WORKDIR /app/backend

COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"

COPY . .

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
