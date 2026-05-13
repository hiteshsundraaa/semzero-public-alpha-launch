from sqlalchemy import create_engine, text
from semzero.crawler.builder import SchemaGraphBuilder

db_url = "sqlite:///test.db"

# Step 1 — seed the database with test tables
engine = create_engine(db_url)
with engine.connect() as conn:
    conn.execute(
        text("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            email VARCHAR(255) NOT NULL,
            name VARCHAR(100),
            created_at TIMESTAMP
        )
    """)
    )
    conn.execute(
        text("""
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            amount NUMERIC,
            status VARCHAR(50),
            created_at TIMESTAMP
        )
    """)
    )
    conn.execute(
        text("""
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY,
            name VARCHAR(255) NOT NULL,
            price NUMERIC,
            stock INTEGER
        )
    """)
    )
    conn.execute(
        text("""
        CREATE TABLE IF NOT EXISTS order_items (
            id INTEGER PRIMARY KEY,
            order_id INTEGER REFERENCES orders(id),
            product_id INTEGER REFERENCES products(id),
            quantity INTEGER,
            unit_price NUMERIC
        )
    """)
    )
    conn.commit()
    print("Database seeded.")

# Step 2 — crawl and build the graph
builder = SchemaGraphBuilder(db_url)
graph = builder.build()
builder.save()

print(f"Tables:  {graph['meta']['table_count']}")
print(f"Nodes:   {graph['meta']['node_count']}")
print(f"Edges:   {graph['meta']['edge_count']}")
print("Saved to data/schema_graph.json")
