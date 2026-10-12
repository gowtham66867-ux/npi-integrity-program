# NPI Integrity console - prototype image (public + synthetic data only; no PHI).
FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 NPI_HOME=/app
WORKDIR /app
COPY pyproject.toml README.md ./
COPY npi_integrity ./npi_integrity
COPY rules ./rules
COPY evals ./evals
RUN pip install --no-cache-dir . && useradd --create-home --uid 10001 npi
# Bake only synthetic demo data into the image. Deployed reviewer actions are stored in Firestore.
RUN python -m npi_integrity.cli demo && python -m evals.run_evals --suites guardrails,agent_faults,injection \
    && chown -R npi:npi /app
USER npi
EXPOSE 8080
CMD ["sh", "-c", "uvicorn npi_integrity.api:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers"]
