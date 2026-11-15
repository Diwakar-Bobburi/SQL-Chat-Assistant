"""Build the deterministic benchmark database (retail.db). Same seed -> identical DB."""
from __future__ import annotations

import random
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE categories (category_id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE products (
  product_id INTEGER PRIMARY KEY, name TEXT NOT NULL, category_id INTEGER NOT NULL,
  price REAL NOT NULL, cost REAL NOT NULL, launched_on TEXT NOT NULL,
  FOREIGN KEY (category_id) REFERENCES categories(category_id));
CREATE TABLE customers (
  customer_id INTEGER PRIMARY KEY, first_name TEXT, last_name TEXT, email TEXT,
  city TEXT, country TEXT, segment TEXT, signup_date TEXT);
CREATE TABLE employees (
  employee_id INTEGER PRIMARY KEY, name TEXT, region TEXT, hire_date TEXT, manager_id INTEGER,
  FOREIGN KEY (manager_id) REFERENCES employees(employee_id));
CREATE TABLE orders (
  order_id INTEGER PRIMARY KEY, customer_id INTEGER NOT NULL, employee_id INTEGER,
  order_date TEXT NOT NULL, status TEXT NOT NULL,
  FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
  FOREIGN KEY (employee_id) REFERENCES employees(employee_id));
CREATE TABLE order_items (
  order_id INTEGER NOT NULL, product_id INTEGER NOT NULL, quantity INTEGER NOT NULL,
  unit_price REAL NOT NULL, discount REAL NOT NULL DEFAULT 0,
  PRIMARY KEY (order_id, product_id),
  FOREIGN KEY (order_id) REFERENCES orders(order_id),
  FOREIGN KEY (product_id) REFERENCES products(product_id));
CREATE TABLE reviews (
  review_id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL, customer_id INTEGER NOT NULL,
  rating INTEGER NOT NULL, review_date TEXT NOT NULL,
  FOREIGN KEY (product_id) REFERENCES products(product_id),
  FOREIGN KEY (customer_id) REFERENCES customers(customer_id));
"""

CATEGORIES = {
    "Electronics": ["Wireless Earbuds", "Smartwatch", "Bluetooth Speaker", "USB-C Hub", "Mechanical Keyboard",
                    "Gaming Mouse", "4K Monitor", "Power Bank", "Webcam"],
    "Home & Kitchen": ["Air Fryer", "Coffee Grinder", "Chef Knife", "Cast Iron Pan", "Blender", "Kettle",
                       "Rice Cooker", "Storage Jars"],
    "Books": ["Data Science Handbook", "Learning SQL", "Deep Learning Basics", "Clean Code", "Atomic Habits",
              "The Pragmatic Programmer", "Designing Data Apps"],
    "Sports": ["Yoga Mat", "Dumbbell Set", "Running Shoes", "Cricket Bat", "Football", "Resistance Bands",
               "Water Bottle"],
    "Clothing": ["Denim Jacket", "Cotton T-Shirt", "Hoodie", "Chinos", "Rain Jacket", "Wool Socks"],
    "Beauty": ["Face Wash", "Sunscreen SPF 50", "Moisturizer", "Hair Oil", "Lip Balm"],
}
PRICE_RANGE = {"Electronics": (15, 450), "Home & Kitchen": (8, 180), "Books": (9, 60),
               "Sports": (6, 140), "Clothing": (8, 120), "Beauty": (3, 40)}
CITIES = {"India": ["Bengaluru", "Mumbai", "Delhi", "Hyderabad", "Chennai", "Pune"],
          "Germany": ["Berlin", "Munich", "Hamburg", "Frankfurt"],
          "USA": ["New York", "Austin", "Seattle", "Chicago"],
          "UK": ["London", "Manchester"], "Netherlands": ["Amsterdam", "Rotterdam"]}
FIRST = ["Aarav", "Diya", "Rohan", "Ananya", "Vikram", "Priya", "Lukas", "Mia", "Jonas", "Emma", "Noah",
         "Olivia", "Liam", "Sophia", "Arjun", "Kavya", "Felix", "Hannah", "James", "Isla", "Sanne", "Daan"]
LAST = ["Sharma", "Reddy", "Iyer", "Patel", "Nair", "Müller", "Schmidt", "Fischer", "Smith", "Johnson",
        "Brown", "Taylor", "de Vries", "Jansen", "Rao", "Gupta", "Weber", "Wilson"]
SEGMENTS = ["Consumer", "Corporate", "Small Business"]
REGIONS = ["South Asia", "Europe", "North America"]


def rand_date(rng: random.Random, start: date, end: date) -> date:
    return start + timedelta(days=rng.randint(0, (end - start).days))


def build(path: Path, seed: int = 42) -> None:
    rng = random.Random(seed)
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)

    # categories + products
    pid = 0
    products = []
    for cid, (cat, items) in enumerate(CATEGORIES.items(), start=1):
        conn.execute("INSERT INTO categories VALUES (?, ?)", (cid, cat))
        lo, hi = PRICE_RANGE[cat]
        for name in items:
            pid += 1
            price = round(rng.uniform(lo, hi), 2)
            cost = round(price * rng.uniform(0.35, 0.75), 2)
            launched = rand_date(rng, date(2021, 1, 1), date(2025, 6, 30)).isoformat()
            products.append((pid, name, cid, price, cost, launched))
    conn.executemany("INSERT INTO products VALUES (?,?,?,?,?,?)", products)

    # employees: 3 regional managers + 9 reps
    emps, eid = [], 0
    managers = {}
    for region in REGIONS:
        eid += 1
        managers[region] = eid
        emps.append((eid, f"{rng.choice(FIRST)} {rng.choice(LAST)}", region,
                     rand_date(rng, date(2018, 1, 1), date(2020, 12, 31)).isoformat(), None))
    for region in REGIONS:
        for _ in range(3):
            eid += 1
            emps.append((eid, f"{rng.choice(FIRST)} {rng.choice(LAST)}", region,
                         rand_date(rng, date(2021, 1, 1), date(2024, 6, 30)).isoformat(), managers[region]))
    conn.executemany("INSERT INTO employees VALUES (?,?,?,?,?)", emps)
    reps_by_region = {r: [e[0] for e in emps if e[2] == r and e[4] is not None] for r in REGIONS}
    region_of_country = {"India": "South Asia", "Germany": "Europe", "UK": "Europe", "Netherlands": "Europe",
                         "USA": "North America"}

    # customers
    customers = []
    countries = list(CITIES)
    weights = [40, 20, 20, 10, 10]
    for cid in range(1, 301):
        country = rng.choices(countries, weights)[0]
        fn, ln = rng.choice(FIRST), rng.choice(LAST)
        email = f"{fn.lower()}.{ln.lower().replace(' ', '').replace('ü', 'u')}{cid}@example.com"
        customers.append((cid, fn, ln, email, rng.choice(CITIES[country]), country,
                          rng.choices(SEGMENTS, [60, 25, 15])[0],
                          rand_date(rng, date(2022, 1, 1), date(2025, 10, 31)).isoformat()))
    conn.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?,?,?)", customers)

    # orders + items. ~15% of customers never order.
    buyers = [c for c in customers if rng.random() > 0.15]
    statuses = ["delivered", "shipped", "cancelled", "returned"]
    orders, items = [], []
    for oid in range(1, 2201):
        c = rng.choice(buyers)
        signup = date.fromisoformat(c[7])
        od = rand_date(rng, max(signup, date(2023, 1, 1)), date(2025, 12, 31))
        if od < signup:
            od = signup
        status = rng.choices(statuses, [78, 8, 9, 5])[0]
        emp = rng.choice(reps_by_region[region_of_country[c[5]]]) if rng.random() > 0.05 else None
        orders.append((oid, c[0], emp, od.isoformat(), status))
        launched_ok = [p for p in products if date.fromisoformat(p[5]) <= od] or products[:5]
        for p in rng.sample(launched_ok, k=min(len(launched_ok), rng.choices([1, 2, 3, 4], [45, 30, 17, 8])[0])):
            qty = rng.choices([1, 2, 3, 5], [65, 22, 9, 4])[0]
            disc = rng.choices([0, 0.05, 0.1, 0.2], [70, 12, 12, 6])[0]
            items.append((oid, p[0], qty, p[3], disc))
    conn.executemany("INSERT INTO orders VALUES (?,?,?,?,?)", orders)
    conn.executemany("INSERT INTO order_items VALUES (?,?,?,?,?)", items)

    # reviews: only for delivered purchases, ~30% of them
    delivered = {o[0]: o for o in orders if o[4] == "delivered"}
    reviews, rid, seen = [], 0, set()
    for oid, prod, *_ in items:
        o = delivered.get(oid)
        if o and (o[1], prod) not in seen and rng.random() < 0.3:
            seen.add((o[1], prod))
            rid += 1
            rd = date.fromisoformat(o[3]) + timedelta(days=rng.randint(3, 40))
            reviews.append((rid, prod, o[1], rng.choices([1, 2, 3, 4, 5], [5, 8, 17, 35, 35])[0], rd.isoformat()))
    conn.executemany("INSERT INTO reviews VALUES (?,?,?,?,?)", reviews)

    conn.commit()
    conn.close()


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "retail.db"
    build(out)
    print(f"Built {out}")
