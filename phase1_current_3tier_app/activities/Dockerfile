FROM python:3.11-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

# Install PostgreSQL client libraries
RUN apt-get update && apt-get install -y libpq-dev gcc && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY shared/requirements.txt .
COPY shared ./shared
COPY activities .
RUN pip install --no-cache-dir -r requirements.txt

COPY init_activities.py .

CMD ["python", "init_activities.py"]

