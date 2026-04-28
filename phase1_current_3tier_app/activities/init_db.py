import psycopg2
import random

conn = psycopg2.connect(
    dbname="travel",
    user="travel",
    password="travelpass",
    host="postgres"
)

cur = conn.cursor()

cur.execute("""
CREATE TABLE IF NOT EXISTS activities (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255),
    description TEXT
);
""")

cur.execute("SELECT COUNT(*) FROM activities;")
count = cur.fetchone()[0]

if count == 0:
    for i in range(100):
        cur.execute(
            "INSERT INTO activities (title, description) VALUES (%s, %s)",
            (f"Activity {i+1}", f"Exciting experience number {i+1}")
        )

conn.commit()
cur.close()
conn.close()

