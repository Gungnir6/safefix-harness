FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SAFEFIX_DATA_DIR=/data

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY examples ./examples
RUN python -m pip install --no-cache-dir . \
    && groupadd --system safefix \
    && useradd --system --gid safefix --uid 10001 --create-home safefix \
    && mkdir -p /data \
    && chown safefix:safefix /data

USER safefix
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=2)"
CMD ["safefix", "serve", "--public-demo", "--host", "0.0.0.0", "--port", "8000"]
