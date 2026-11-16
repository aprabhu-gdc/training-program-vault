# Claude analytics connector. Build from the repository root:
#   docker build -f apps/analytics_mcp/Dockerfile -t training-analytics-connector .
# The root .dockerignore is an allowlist, so .env, raw/, wiki/ and other repo
# content never enter the build context, let alone the image.
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    FASTMCP_HOME=/tmp/fastmcp

WORKDIR /app

COPY apps/analytics_mcp/requirements.txt /tmp/requirements.txt
RUN pip install -r /tmp/requirements.txt

# Only the code the connector imports.
COPY apps/__init__.py apps/__init__.py
COPY apps/analytics_mcp apps/analytics_mcp
COPY packages packages
COPY teams_bot/__init__.py teams_bot/__init__.py
COPY teams_bot/services/__init__.py teams_bot/services/analytics.py teams_bot/services/concept_labels.py teams_bot/services/

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin app
USER 10001

EXPOSE 8000
CMD ["uvicorn", "apps.analytics_mcp.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
