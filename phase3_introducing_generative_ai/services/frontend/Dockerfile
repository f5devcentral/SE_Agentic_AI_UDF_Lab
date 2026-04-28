
FROM python:3.12-slim

LABEL maintainer="f.chmainy@f5.com"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl vim \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY services/shared/requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir setuptools \
    && pip install --no-cache-dir -r requirements.txt

COPY services/frontend/app.py /app/app.py
COPY services/frontend/templates /app/templates
COPY services/shared /app/shared
 
ENV PYTHONUNBUFFERED=1

CMD ["python", "/app/app.py"]
