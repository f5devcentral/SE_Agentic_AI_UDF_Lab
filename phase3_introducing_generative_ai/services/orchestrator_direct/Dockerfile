FROM python:3.12-slim

WORKDIR /app

COPY services/shared /app/shared
COPY services/shared/requirements.txt /app/requirements.txt

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl vim \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip tiktoken fastmcp setuptools wheel \
    && pip install --no-cache-dir setuptools \
    && pip install --no-cache-dir -r requirements.txt

RUN pip install https://docs.aisecurity.f5.com/calypsoai-2.82.0-py3-none-any.whl

COPY services/orchestrator_direct/*.py /app/

ENV PYTHONUNBUFFERED=1

CMD ["python", "app.py"]

