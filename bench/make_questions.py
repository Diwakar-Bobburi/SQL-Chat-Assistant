"""Source of truth for the benchmark questions. Writes questions.json.

R = revenue expression; REV = the delivered/shipped filter. Kept as constants so all
gold queries apply the glossary definitions identically.
"""
import json
from pathlib import Path

R = "oi.quantity * oi.unit_price * (1 - oi.discount)"
REV = "o.status IN ('delivered', 'shipped')"

Q = [
    # ------------------------------------------------------------------ easy
    ("e01", "easy", "How many customers are there?", "SELECT COUNT(*) FROM customers"),
    ("e02", "easy", "How many orders were cancelled?", "SELECT COUNT(*) FROM orders WHERE status = 'cancelled'"),
    ("e03", "easy", "List the names of all products in the Books category.",
     "SELECT p.name FROM products p JOIN categories c ON c.category_id = p.category_id WHERE c.name = 'Books'"),
    ("e04", "easy", "How many customers are from Germany?", "SELECT COUNT(*) FROM customers WHERE country = 'Germany'"),
    ("e05", "easy", "What is the most expensive product? Return its name and price.",
     "SELECT name, price FROM products ORDER BY price DESC LIMIT 1"),
    ("e06", "easy", "How many products are in each category? Return the category name and the product count.",
     "SELECT c.name, COUNT(*) FROM products p JOIN categories c ON c.category_id = p.category_id GROUP BY c.category_id, c.name"),
    ("e07", "easy", "What is the average product price?", "SELECT AVG(price) FROM products"),
    ("e08", "easy", "How many orders were placed in 2024?",
     "SELECT COUNT(*) FROM orders WHERE strftime('%Y', order_date) = '2024'"),
    ("e09", "easy", "List the names of employees who have no manager.",
     "SELECT name FROM employees WHERE manager_id IS NULL"),
    ("e10", "easy", "Which cities in India do our customers come from? Return each city once.",
     "SELECT DISTINCT city FROM customers WHERE country = 'India'"),
    ("e11", "easy", "How many reviews have a rating of 5?", "SELECT COUNT(*) FROM reviews WHERE rating = 5"),
    ("e12", "easy", "How many customers signed up in 2025?",
     "SELECT COUNT(*) FROM customers WHERE strftime('%Y', signup_date) = '2025'"),
    ("e13", "easy", "List the 5 cheapest products with their prices, cheapest first.",
     "SELECT name, price FROM products ORDER BY price ASC LIMIT 5"),
    ("e14", "easy", "How many customers are in each segment? Return the segment and the count.",
     "SELECT segment, COUNT(*) FROM customers GROUP BY segment"),
    ("e15", "easy", "What is the date of the earliest order?", "SELECT MIN(order_date) FROM orders"),
    # ---------------------------------------------------------------- medium
    ("m01", "medium", "What is our total revenue?",
     f"SELECT SUM({R}) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {REV}"),
    ("m02", "medium", "What is the revenue of each category? Return the category name and revenue, highest revenue first.",
     f"""SELECT c.name, SUM({R}) AS revenue FROM order_items oi JOIN orders o ON o.order_id = oi.order_id
     JOIN products p ON p.product_id = oi.product_id JOIN categories c ON c.category_id = p.category_id
     WHERE {REV} GROUP BY c.category_id, c.name ORDER BY revenue DESC"""),
    ("m03", "medium", "Which 5 customers placed the most orders (any status)? Return first name, last name and number of orders, most orders first.",
     """SELECT c.first_name, c.last_name, COUNT(*) AS n FROM orders o JOIN customers c ON c.customer_id = o.customer_id
     GROUP BY c.customer_id, c.first_name, c.last_name ORDER BY n DESC LIMIT 5"""),
    ("m04", "medium", "What is the average review rating for each category? Return category name and average rating.",
     """SELECT c.name, AVG(r.rating) FROM reviews r JOIN products p ON p.product_id = r.product_id
     JOIN categories c ON c.category_id = p.category_id GROUP BY c.category_id, c.name"""),
    ("m05", "medium", "How many orders did each sales region handle? Return region and number of orders.",
     "SELECT e.region, COUNT(*) FROM orders o JOIN employees e ON e.employee_id = o.employee_id GROUP BY e.region"),
    ("m06", "medium", "What was the revenue for each month of 2024? Return the month as 'YYYY-MM' and the revenue, in chronological order.",
     f"""SELECT strftime('%Y-%m', o.order_date) AS month, SUM({R}) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id
     WHERE {REV} AND strftime('%Y', o.order_date) = '2024' GROUP BY month ORDER BY month"""),
    ("m07", "medium", "How many customers have never written a review?",
     "SELECT COUNT(*) FROM customers c WHERE NOT EXISTS (SELECT 1 FROM reviews r WHERE r.customer_id = c.customer_id)"),
    ("m08", "medium", "How many customers have never placed an order?",
     "SELECT COUNT(*) FROM customers c WHERE NOT EXISTS (SELECT 1 FROM orders o WHERE o.customer_id = c.customer_id)"),
    ("m09", "medium", "What is our average order value?",
     f"SELECT SUM({R}) / COUNT(DISTINCT o.order_id) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE {REV}"),
    ("m10", "medium", "List the first and last names of Corporate segment customers who live in Bengaluru.",
     "SELECT first_name, last_name FROM customers WHERE city = 'Bengaluru' AND segment = 'Corporate'"),
    ("m11", "medium", "Which customer country generates the most revenue? Return the country and its revenue.",
     f"""SELECT c.country, SUM({R}) AS revenue FROM order_items oi JOIN orders o ON o.order_id = oi.order_id
     JOIN customers c ON c.customer_id = o.customer_id WHERE {REV} GROUP BY c.country ORDER BY revenue DESC LIMIT 1"""),
    ("m12", "medium", "For each order status, how many orders are there and what percentage of all orders is that? Return status, count and percentage.",
     "SELECT status, COUNT(*), 100.0 * COUNT(*) / (SELECT COUNT(*) FROM orders) FROM orders GROUP BY status"),
    ("m13", "medium", "Which products have at least 20 reviews and an average rating below 3.9? Return product name and average rating.",
     """SELECT p.name, AVG(r.rating) FROM reviews r JOIN products p ON p.product_id = r.product_id
     GROUP BY p.product_id, p.name HAVING COUNT(*) >= 20 AND AVG(r.rating) < 3.9"""),
    ("m14", "medium", "How many distinct products has the customer with id 17 ever ordered, across all their orders?",
     "SELECT COUNT(DISTINCT oi.product_id) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id WHERE o.customer_id = 17"),
    ("m15", "medium", "How many units of each Electronics product were sold? Return product name and units sold.",
     """SELECT p.name, SUM(oi.quantity) FROM order_items oi JOIN orders o ON o.order_id = oi.order_id
     JOIN products p ON p.product_id = oi.product_id JOIN categories c ON c.category_id = p.category_id
     WHERE o.status IN ('delivered', 'shipped') AND c.name = 'Electronics' GROUP BY p.product_id, p.name"""),
    ("m16", "medium", "Which employees handled more than 230 orders (any status)? Return employee name and number of orders.",
     """SELECT e.name, COUNT(*) FROM orders o JOIN employees e ON e.employee_id = o.employee_id
     GROUP BY e.employee_id, e.name HAVING COUNT(*) > 230"""),
    ("m17", "medium", "What is the gross profit of each category? Return the category name and gross profit, highest first.",
     f"""SELECT c.name, SUM({R}) - SUM(oi.quantity * p.cost) AS gp FROM order_items oi JOIN orders o ON o.order_id = oi.order_id
     JOIN products p ON p.product_id = oi.product_id JOIN categories c ON c.category_id = p.category_id
     WHERE {REV} GROUP BY c.category_id, c.name ORDER BY gp DESC"""),
    ("m18", "medium", "What percentage of all orders had a discount on at least one item?",
     """SELECT 100.0 * COUNT(DISTINCT CASE WHEN oi.discount > 0 THEN oi.order_id END) / (SELECT COUNT(*) FROM orders)
     FROM order_items oi"""),
    # ------------------------------------------------------------------ hard
    ("h01", "hard", "For each year, what was the revenue and the year-over-year growth in percent? Return year, revenue and growth percentage (NULL for the first year), in chronological order.",
     f"""WITH y AS (SELECT strftime('%Y', o.order_date) AS yr, SUM({R}) AS rev FROM order_items oi
     JOIN orders o ON o.order_id = oi.order_id WHERE {REV} GROUP BY yr)
     SELECT yr, rev, 100.0 * (rev - LAG(rev) OVER (ORDER BY yr)) / LAG(rev) OVER (ORDER BY yr) FROM y ORDER BY yr"""),
    ("h02", "hard", "Who are the top 2 customers by revenue in each country? Return country, first name, last name and revenue.",
     f"""WITH cr AS (SELECT c.country, c.first_name, c.last_name, SUM({R}) AS rev,
       ROW_NUMBER() OVER (PARTITION BY c.country ORDER BY SUM({R}) DESC) AS rn
     FROM order_items oi JOIN orders o ON o.order_id = oi.order_id JOIN customers c ON c.customer_id = o.customer_id
     WHERE {REV} GROUP BY c.customer_id)
     SELECT country, first_name, last_name, rev FROM cr WHERE rn <= 2"""),
    ("h03", "hard", "Which sales rep generated the most revenue in each region? Return region, rep name and revenue.",
     f"""WITH er AS (SELECT e.region, e.name, SUM({R}) AS rev,
       RANK() OVER (PARTITION BY e.region ORDER BY SUM({R}) DESC) AS rk
     FROM order_items oi JOIN orders o ON o.order_id = oi.order_id JOIN employees e ON e.employee_id = o.employee_id
     WHERE {REV} AND e.manager_id IS NOT NULL GROUP BY e.employee_id)
     SELECT region, name, rev FROM er WHERE rk = 1"""),
    ("h04", "hard", "For every sales rep, show the rep's name and their manager's name.",
     "SELECT e.name, m.name FROM employees e JOIN employees m ON m.employee_id = e.manager_id"),
    ("h05", "hard", "How many customers placed at least one order in each of the years 2023, 2024 and 2025?",
     """SELECT COUNT(*) FROM (SELECT customer_id FROM orders WHERE strftime('%Y', order_date) IN ('2023','2024','2025')
     GROUP BY customer_id HAVING COUNT(DISTINCT strftime('%Y', order_date)) = 3)"""),
    ("h06", "hard", "On average, how many days pass between a customer's signup date and their first order?",
     """SELECT AVG(julianday(f.first_order) - julianday(c.signup_date)) FROM customers c
     JOIN (SELECT customer_id, MIN(order_date) AS first_order FROM orders GROUP BY customer_id) f ON f.customer_id = c.customer_id"""),
    ("h07", "hard", "Among products that had revenue in both 2024 and 2025, which had 2025 revenue more than three times their 2024 revenue? Return product name, 2024 revenue and 2025 revenue.",
     f"""SELECT p.name,
       SUM(CASE WHEN strftime('%Y', o.order_date) = '2024' THEN {R} ELSE 0 END) AS r24,
       SUM(CASE WHEN strftime('%Y', o.order_date) = '2025' THEN {R} ELSE 0 END) AS r25
     FROM order_items oi JOIN orders o ON o.order_id = oi.order_id JOIN products p ON p.product_id = oi.product_id
     WHERE {REV} GROUP BY p.product_id, p.name HAVING r24 > 0 AND r25 > 3 * r24"""),
    ("h08", "hard", "What percentage of total revenue comes from the top 10 customers by revenue?",
     f"""WITH cr AS (SELECT o.customer_id, SUM({R}) AS rev FROM order_items oi JOIN orders o ON o.order_id = oi.order_id
     WHERE {REV} GROUP BY o.customer_id)
     SELECT 100.0 * (SELECT SUM(rev) FROM (SELECT rev FROM cr ORDER BY rev DESC LIMIT 10)) / (SELECT SUM(rev) FROM cr)"""),
    ("h09", "hard", "In each category, which product has the highest average rating among products with at least 15 reviews? Return category name, product name and average rating.",
     """WITH pr AS (SELECT c.name AS cat, p.name AS prod, AVG(r.rating) AS avg_r,
       RANK() OVER (PARTITION BY c.category_id ORDER BY AVG(r.rating) DESC) AS rk
     FROM reviews r JOIN products p ON p.product_id = r.product_id JOIN categories c ON c.category_id = p.category_id
     GROUP BY p.product_id HAVING COUNT(*) >= 15)
     SELECT cat, prod, avg_r FROM pr WHERE rk = 1"""),
    ("h10", "hard", "Among customers who have placed at least one order (any status), what percentage placed more than 10 orders?",
     """SELECT 100.0 * SUM(CASE WHEN n > 10 THEN 1 ELSE 0 END) / COUNT(*)
     FROM (SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id)"""),
    ("h11", "hard", "How many active customers have revenue above the average revenue per active customer?",
     f"""WITH cr AS (SELECT o.customer_id, SUM({R}) AS rev FROM order_items oi JOIN orders o ON o.order_id = oi.order_id
     WHERE {REV} GROUP BY o.customer_id)
     SELECT COUNT(*) FROM cr WHERE rev > (SELECT AVG(rev) FROM cr)"""),
    ("h12", "hard", "Show the cumulative (running total) revenue by month for 2025. Return the month as 'YYYY-MM' and the running total, in chronological order.",
     f"""WITH m AS (SELECT strftime('%Y-%m', o.order_date) AS month, SUM({R}) AS rev FROM order_items oi
     JOIN orders o ON o.order_id = oi.order_id WHERE {REV} AND strftime('%Y', o.order_date) = '2025' GROUP BY month)
     SELECT month, SUM(rev) OVER (ORDER BY month) FROM m ORDER BY month"""),
]

# Questions where row order is part of the answer (asked for, and no ties in the sort key).
# Everything else is compared as an unordered multiset of rows.
ORDERED = {"e13", "m02", "m06", "m17", "h01", "h12"}

if __name__ == "__main__":
    out = [{"id": i, "difficulty": d, "question": q, "gold_sql": " ".join(s.split()), "order_matters": i in ORDERED}
           for i, d, q, s in Q]
    path = Path(__file__).parent / "questions.json"
    path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {len(out)} questions to {path}")
