CREATE TABLE IF NOT EXISTS activities (
    id SERIAL PRIMARY KEY,
    title VARCHAR(255) NOT NULL,
    description TEXT,
    city VARCHAR(100)
);

INSERT INTO activities (title, description, city) VALUES
    ('Colosseum Tour', 'Explore the iconic ancient amphitheatre', 'Rome'),
    ('Vatican Museums', 'World-renowned art and history', 'Rome'),
    ('Trastevere Food Walk', 'Taste authentic Roman street food', 'Rome'),
    ('Eiffel Tower', 'Visit the symbol of Paris', 'Paris'),
    ('Louvre Museum', 'Home of the Mona Lisa', 'Paris'),
    ('Seine River Cruise', 'See Paris from the water', 'Paris'),
    ('Sagrada Familia', 'Gaudí''s unfinished masterpiece', 'Barcelona'),
    ('Park Güell', 'Mosaic terraces with city views', 'Barcelona'),
    ('City Walking Tour', 'Explore the historic city center on foot', NULL),
    ('Cooking Class', 'Learn to cook traditional local dishes', NULL);
