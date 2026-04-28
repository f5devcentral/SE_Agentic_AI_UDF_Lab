FROM python:3.12-slim

WORKDIR /app

COPY services/etl-job/requirements.txt /app/requirements.txt

RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir setuptools \
    && pip install --no-cache-dir -r requirements.txt


COPY services/etl-job/job.py /app/job.py

ENV PYTHONUNBUFFERED=1

CMD ["python", "job.py"]

