import logging
import os
from typing import List

import psycopg2
import requests
from flask import Flask, request, jsonify

from shared.logging import configure_logging
from shared.metrics import register_metrics
from shared.tracing import init_tracing


SERVICE_NAME = "rag-service"
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
EMBED_MODEL = os.getenv("EMBED_MODEL", "nomic-embed-text")
PG_HOST = os.getenv("PG_HOST", "postgres")
PG_DB = os.getenv("PG_DB", "ragdb")
PG_USER = os.getenv("PG_USER", "postgres")
PG_PASSWORD = os.getenv("PG_PASSWORD", "postgrespass")
PG_PORT = int(os.getenv("PG_PORT", "5432"))

app = Flask(__name__)
configure_logging(SERVICE_NAME)
meter = register_metrics(app, SERVICE_NAME)
init_tracing(app, SERVICE_NAME)
logger = logging.getLogger(SERVICE_NAME)


def get_pg_conn():
    return psycopg2.connect(
        host=PG_HOST, dbname=PG_DB, user=PG_USER, password=PG_PASSWORD, port=PG_PORT
    )


def get_embedding(text: str) -> List[float]:
    resp = requests.post(
        f"{OLLAMA_URL}/api/embeddings",
        json={"model": EMBED_MODEL, "prompt": text},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["embedding"]


@app.route("/health", methods=["GET"])
def health():
    return {"status": "ok"}, 200


@app.route("/search", methods=["POST"])
def search():
    body = request.get_json(force=True)
    query = body.get("query", "")
    if not query:
        return jsonify({"error": "query is required"}), 400

    embedding = get_embedding(query)
    embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"

    sql = """
        SELECT id, content
        FROM documents
        ORDER BY embedding <-> %s::vector
        LIMIT 5
    """

    results = []
    with get_pg_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, (embedding_str,))
            rows = cur.fetchall()
            for r in rows:
                results.append({"id": r[0], "content": r[1]})

    return jsonify({"query": query, "results": results}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)

