FROM python:3.12-slim

WORKDIR /app

COPY services/shared /app/shared
COPY services/shared/requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir --upgrade pip tiktoken setuptools wheel \
    && pip install --no-cache-dir setuptools \
    && pip install --no-cache-dir -r requirements.txt


COPY services/orchestrator/app.py /app/app.py

ENV PYTHONUNBUFFERED=1

CMD ["python", "app.py"]

