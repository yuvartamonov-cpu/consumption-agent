import sqlite3

def save_user_brand_link(conn: sqlite3.Connection, brand: str, store_name: str, url: str, category: str = None):
    conn.execute(
        "INSERT INTO user_brand_links (brand, store_name, url, category) VALUES (?, ?, ?, ?)",
        (brand.lower().strip(), store_name, url, category)
    )
    conn.commit()

def get_user_brand_links(conn: sqlite3.Connection, brand: str) -> list[dict]:
    brand = brand.lower().strip()
    rows = conn.execute(
        "SELECT store_name, url, category FROM user_brand_links WHERE brand = ?", 
        (brand,)
    ).fetchall()
    return [{"store": r[0], "url": r[1], "category": r[2]} for r in rows]
