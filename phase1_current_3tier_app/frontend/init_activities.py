import psycopg2
import os

# PostgreSQL connection
conn = psycopg2.connect(
    host=os.getenv("PG_HOST", "postgres"),
    user=os.getenv("PG_USER", "travel"),
    password=os.getenv("PG_PASSWORD", "travelpass"),
    dbname=os.getenv("PG_DB", "travel")
)
cur = conn.cursor()

# Create table if it does not exist
cur.execute("""
CREATE TABLE IF NOT EXISTS activities (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255),
    description TEXT
)
""")

# Check if table is already populated
cur.execute("SELECT COUNT(*) FROM activities;")
count = cur.fetchone()[0]

if count == 0:
    activities = [
        ("Hiking the Blue Mountains", "Guided hiking tour through the beautiful Blue Mountains."),
        ("City Museum Visit", "Explore the main city museum with historical exhibits."),
        ("Boat Tour on the River", "Relaxing 2-hour boat tour along the river."),
        ("Wine Tasting at Vineyard", "Sample local wines with expert guidance."),
        ("Cycling Tour", "Join a cycling tour covering scenic spots."),
        ("Cooking Class", "Learn to cook local dishes with a professional chef."),
        ("Art Gallery Visit", "Visit modern and classic art collections."),
        ("Kayaking Adventure", "2-hour kayaking experience on the lake."),
        ("Historic Castle Tour", "Guided tour inside a medieval castle."),
        ("Local Market Exploration", "Discover local crafts, foods, and souvenirs."),
        ("Mountain Photography Workshop", "Capture stunning landscapes with guidance."),
        ("Street Food Tour", "Taste local specialties in a guided tour."),
        ("Beach Yoga Session", "Morning yoga session on the beach."),
        ("Horseback Riding", "Scenic horseback ride through countryside trails."),
        ("Zipline Adventure", "Exciting zipline over the forest canopy."),
        ("Scuba Diving Intro", "Beginner scuba diving course with instructor."),
        ("Theater Performance", "Enjoy a local theater performance in town."),
        ("Botanical Garden Walk", "Guided walk through exotic plants and gardens."),
        ("Wildlife Safari", "Observe wildlife in natural habitats."),
        ("Night Sky Stargazing", "Astronomy session with telescopes and guides.")
    ]

    for title, description in activities:
        cur.execute(
            "INSERT INTO activities (title, description) VALUES (%s, %s)",
            (title, description)
        )

conn.commit()
cur.close()
conn.close()
print("20 activities seeded successfully.")

