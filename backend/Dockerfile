FROM python:3.12-slim-trixie AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
# WeasyPrint needs Pango; Noto covers Arabic shaping in generated PDFs. scripts/backup.sh needs
# pg_dump (17 on trixie; it must be at least the server's major version) and age.
RUN apt-get update \
 && apt-get upgrade -y \
 && apt-get install -y --no-install-recommends libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0 \
      fonts-noto-core fonts-dejavu-core postgresql-client age \
 && rm -rf /var/lib/apt/lists/*
WORKDIR /app

FROM base AS deps
COPY pyproject.toml ./
RUN mkdir app && touch app/__init__.py && pip install . && pip uninstall -y stemtrack && rm -rf app

FROM base AS runtime
COPY --from=deps /usr/local /usr/local
ENV PYTHONPATH=/app
COPY . .
RUN useradd --system --uid 10001 --home /app stem \
 && mkdir -p /app/var/storage /app/var/backups && chown -R stem /app/var
USER stem
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"
# Override the command for the worker:  arq app.worker.WorkerSettings
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
