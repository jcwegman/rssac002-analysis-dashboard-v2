FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    RSSAC002_DB=/data/dashboard.sqlite3

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY rssac002_dashboard ./rssac002_dashboard
# Git is needed for sources that are Git repositories.
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/* \
 && pip install --no-cache-dir . \
 && useradd --system --uid 999 --create-home dashboard \
 && mkdir /data && chown dashboard /data

USER dashboard
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz')"
CMD ["rssac002-dashboard", "serve", "--host", "0.0.0.0", "--port", "8000"]
