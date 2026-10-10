# syntax=docker/dockerfile:1

FROM python:3.11-slim

# Dependencies first: this layer is cached unless requirements.txt changes,
# so code edits rebuild in seconds rather than reinstalling pandas each time.
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY trendlab/ ./trendlab/
COPY README.md ./

# Price data is cached to disk. Keep it on a volume so a restart does not
# re-fetch every symbol -- Yahoo rate-limits datacenter IPs hard.
ENV TRENDLAB_CACHE_DIR=/data/cache \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
RUN mkdir -p /data/cache && useradd --create-home --uid 10001 trendlab \
    && chown -R trendlab:trendlab /data /app
USER trendlab
VOLUME ["/data"]

EXPOSE 8000

# The platform decides the port; default to 8000 when it does not say.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,os,sys; \
sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",8000)}/api/health', timeout=4).status==200 else 1)"

CMD ["sh", "-c", "exec python -m uvicorn trendlab.server.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
