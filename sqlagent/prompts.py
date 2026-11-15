"""Prompt templates.

Few-shot examples use a *different* schema (a library DB) on purpose: examples drawn
from the benchmark DB would leak answers and inflate the score.
"""

SYSTEM = """You are an expert data analyst who writes SQLite queries.
Given a database schema and a question, write ONE SQLite SELECT query that answers it.

Rules:
- Use only tables and columns that exist in the schema.
- SQLite dialect: dates are TEXT 'YYYY-MM-DD'; use strftime('%Y', col), date(), julianday(). No ILIKE, no DATE_TRUNC.
- Return exactly the columns the question asks for, in the order asked, and nothing extra.
- If business definitions are given, follow them exactly.
- Use ORDER BY only when the question asks for an ordering or a top-N.
- Reply with the query in a single ```sql code block and nothing else."""

FEW_SHOT = [
    ("""CREATE TABLE authors (author_id INTEGER PRIMARY KEY, name TEXT, country TEXT);
CREATE TABLE books (book_id INTEGER PRIMARY KEY, title TEXT, author_id INTEGER, published TEXT, price REAL,
  FOREIGN KEY (author_id) REFERENCES authors(author_id));
CREATE TABLE loans (loan_id INTEGER PRIMARY KEY, book_id INTEGER, loan_date TEXT, returned INTEGER,
  FOREIGN KEY (book_id) REFERENCES books(book_id));""",
     "Which 3 authors have the most loans in 2024? Show author name and number of loans.",
     """SELECT a.name, COUNT(*) AS loans
FROM loans l JOIN books b ON b.book_id = l.book_id JOIN authors a ON a.author_id = b.author_id
WHERE strftime('%Y', l.loan_date) = '2024'
GROUP BY a.author_id, a.name
ORDER BY loans DESC
LIMIT 3"""),
    (None,
     "List titles of books that have never been loaned.",
     """SELECT b.title
FROM books b
WHERE NOT EXISTS (SELECT 1 FROM loans l WHERE l.book_id = b.book_id)"""),
    (None,
     "For each country, what share of its authors' books cost more than 20? Return country and the percentage.",
     """SELECT a.country, ROUND(100.0 * SUM(CASE WHEN b.price > 20 THEN 1 ELSE 0 END) / COUNT(*), 2) AS pct
FROM books b JOIN authors a ON a.author_id = b.author_id
GROUP BY a.country"""),
]


def few_shot_messages() -> list[dict]:
    msgs, schema = [], None
    for s, q, sql in FEW_SHOT:
        schema = s or schema
        msgs.append({"role": "user", "content": f"Schema:\n{schema}\n\nQuestion: {q}"})
        msgs.append({"role": "assistant", "content": f"```sql\n{sql}\n```"})
    return msgs


def user_prompt(schema: str, question: str, glossary: str = "") -> str:
    parts = [f"Schema:\n{schema}"]
    if glossary:
        parts.append(f"Business definitions:\n{glossary}")
    parts.append(f"Question: {question}")
    return "\n\n".join(parts)


def repair_prompt(error: str) -> str:
    return (f"That query failed:\n{error}\n\n"
            "Fix it. Check table/column names against the schema. Reply with the corrected query only.")


def empty_check_prompt() -> str:
    return ("That query ran but returned 0 rows. Re-check filters, join conditions and literal values "
            "(case, spelling, date format) against the sample rows. If 0 rows is genuinely the right "
            "answer, repeat the same query. Reply with the query only.")
