import json
import logging
import os

import boto3
import psycopg2
import requests
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.instrumentation.requests import RequestsInstrumentor

SERVICE_NAME = "etl-job"

# S3 / MinIO
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://minio:9000")
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID", "demoaccess")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY", "demosecret")
S3_BUCKET = os.getenv("S3_BUCKET", "travel-data")

# Postgres
PG_HOST = os.getenv("PG_HOST", "postgres")
PG_DB = os.getenv("PG_DB", "ragdb")
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASSWORD = os.getenv("PG_PASSWORD", "postgrespass")
PG_PORT = int(os.getenv("PG_PORT", "5432"))

# Ollama embeddings
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")

# Tracing
OTEL_ENDPOINT = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317")


def init_tracing():
    resource = Resource.create({"service.name": SERVICE_NAME})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True)
    processor = BatchSpanProcessor(exporter)
    provider.add_span_processor(processor)
    trace.set_tracer_provider(provider)
    RequestsInstrumentor().instrument()


def get_embedding(text: str):
    resp = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["embedding"]


def main():
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(SERVICE_NAME)
    init_tracing()
    tracer = trace.get_tracer(__name__)

    # Initialize S3 client
    s3 = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=AWS_ACCESS_KEY_ID,
        aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    )

    # Connect to Postgres
    conn = psycopg2.connect(
        host=PG_HOST, dbname=PG_DB, user=PG_USER, password=PG_PASSWORD, port=PG_PORT
    )
    cur = conn.cursor()

    with tracer.start_as_current_span("etl-run"):
        try:
            resp = s3.list_objects_v2(Bucket=S3_BUCKET)
        except s3.exceptions.NoSuchBucket:
            logger.error(f"S3 bucket '{S3_BUCKET}' does not exist!")
            return

        for obj in resp.get("Contents", []):
            key = obj["Key"]
            logger.info("Processing object %s", key)
            body = s3.get_object(Bucket=S3_BUCKET, Key=key)["Body"].read()

            try:
                # accept either plain text or JSON {"content": "..."}
                try:
                    data = json.loads(body.decode("utf-8"))
                    content = data.get("content", body.decode("utf-8"))
                except json.JSONDecodeError:
                    content = body.decode("utf-8")

                try:
                    emb = get_embedding(content)
                    if len(emb) != 768:
                        raise ValueError(f"Expected 768 dims, got {len(emb)}")
                    emb_str = "[" + ",".join(f"{x:.6f}" for x in emb) + "]"

                    cur.execute(
                        "INSERT INTO documents (content, embedding) VALUES (%s, %s::vector)",
                        (content, emb_str),
                    )
                    conn.commit()
                except Exception as e:
                    conn.rollback()
                    logger.exception(f"Failed embedding insertion for {key}: {e}")

            except Exception as e:
                logger.exception("Failed to process %s: %s", key, e)

        cur.close()
        conn.close()
        logger.info("ETL completed")


if __name__ == "__main__":
    main()
