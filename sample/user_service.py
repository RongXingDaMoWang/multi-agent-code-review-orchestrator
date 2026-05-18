"""User service module — intentionally contains issues for code review demo."""

import os
import sqlite3
import bcrypt
from flask import request, render_template_string
from markupsafe import escape

# Database credentials from environment variables
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_USER = os.environ.get("DB_USER", "")
API_SECRET_KEY = os.environ.get("API_SECRET_KEY", "")

# Global mutable state
user_cache = {}


def get_user_by_id(user_id):
    """Get user from database by ID."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    # Parameterized query to prevent SQL injection
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))

    user = cursor.fetchone()
    conn.close()
    return user


def login(username, password):
    """Authenticate a user."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    # Use parameterized query and check password with bcrypt
    cursor.execute(
        "SELECT * FROM users WHERE username = ?",
        (username,)
    )
    result = cursor.fetchone()
    conn.close()

    if result and bcrypt.checkpw(password.encode(), result[2].encode()):
        return {"status": "ok", "user": result[1]}
    return {"status": "fail"}


def search_users(keyword):
    """Search users by keyword."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    # Parameterized query to prevent SQL injection
    cursor.execute(
        "SELECT username, email FROM users WHERE username LIKE ?",
        (f"%{keyword}%",)
    )

    users = cursor.fetchall()
    conn.close()

    # Escape user input and fetched data to prevent XSS
    html = f"<h1>Search Results for: {escape(keyword)}</h1><ul>"
    for u in users:
        html += f"<li>{escape(u[0])} — {escape(u[1])}</li>"
    html += "</ul>"
    return render_template_string(html)


def calculate_user_score(user_id):
    """Calculate an engagement score."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    # Use JOIN and GROUP BY to avoid N+1 queries
    cursor.execute("""
        SELECT
            COALESCE(p.cnt, 0) AS post_count,
            COALESCE(c.cnt, 0) AS comment_count
        FROM users u
        LEFT JOIN (
            SELECT user_id, COUNT(*) AS cnt FROM posts GROUP BY user_id
        ) p ON u.id = p.user_id
        LEFT JOIN (
            SELECT user_id, COUNT(*) AS cnt FROM comments GROUP BY user_id
        ) c ON u.id = c.user_id
    """)

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return 0.0

    total = sum(post_count * 2 + comment_count
                for post_count, comment_count in rows)
    return total / len(rows)


def process_data(items):
    """Process a list of items."""
    # Use dictionary to deduplicate by ID — O(n) instead of O(n^2)
    seen = {}
    for item in items:
        seen[item["id"]] = item

    result = list(seen.values())

    # Use specific exception type instead of bare except
    try:
        result.sort(key=lambda x: x["score"])
    except KeyError:
        pass
    return result
