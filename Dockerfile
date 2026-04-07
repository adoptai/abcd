FROM python:3.11-slim

WORKDIR /workspace

# Install SDK
COPY adopt_sdk/ /opt/adopt_sdk/
ENV PYTHONPATH="/opt:${PYTHONPATH}"
ENV PYTHONUNBUFFERED=1

# Default env vars (overridden at sandbox creation)
ENV ADOPT_ORG_ID=""
ENV ADOPT_EXECUTION_TOKEN=""
ENV ADOPT_PERMISSIONS="{}"
ENV ADOPT_LAMBDA_ID=""
ENV ADOPT_EXECUTION_ID=""

CMD ["sleep", "infinity"]
