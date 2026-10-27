FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --create-home seance

WORKDIR /app
COPY seance ./seance
COPY web ./web

ENV PYTHONUNBUFFERED=1 \
    SEANCE_CACHE_DIR=/home/seance/cache \
    SEANCE_TRUST_PROXY=1

USER seance
EXPOSE 8666
HEALTHCHECK CMD python -c "import urllib.request,os; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8666\")}/healthz', timeout=3)"
CMD ["sh", "-c", "python -m seance serve --host 0.0.0.0 --port ${PORT:-8666} --warm"]
