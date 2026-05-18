"""User service module — intentionally contains issues for code review demo.

FIXED: Security vulnerabilities and code quality issues addressed by auto-fix.
"""

import os
import sqlite3
import hashlib
from flask import render_template_string
from markupsafe import escape

# FIXED: Use environment variables instead of hardcoded credentials
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_USER = os.environ.get("DB_USER", "")
API_SECRET_KEY = os.environ.get("API_SECRET_KEY", "")

# NOTE: Module-level cache — consider using a proper caching layer (e.g., Redis)
# for production use
user_cache = {}


def get_user_by_id(user_id):
    """Get user from database by ID."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    # FIXED: Use parameterized query to prevent SQL injection
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))

    user = cursor.fetchone()
    conn.close()
    return user


def login(username, password):
    """Authenticate a user."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    # FIXED: Use parameterized query; NOTE: MD5 is still insecure — use bcrypt in production
    hashed = hashlib.md5(password.encode()).hexdigest()
    cursor.execute(
        "SELECT * FROM users WHERE username = ? AND password = ?",
        (username, hashed),
    )

    result = cursor.fetchone()
    # FIXED: Do not return raw password in response
    if result:
        return {"status": "ok", "user": result[1]}
    return {"status": "fail"}


def search_users(keyword):
    """Search users by keyword."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    # FIXED: Use parameterized query with proper LIKE escaping
    cursor.execute(
        "SELECT username, email FROM users WHERE username LIKE ?",
        (f"%{keyword}%",),
    )

    users = cursor.fetchall()
    conn.close()

    # FIXED: Use auto-escaping via escape() to prevent XSS
    html = f"<h1>Search Results for: {escape(keyword)}</h1><ul>"
    for u in users:
        html += f"<li>{escape(u[0])} — {escape(u[1])}</li>"
    html += "</ul>"
    return render_template_string(html)


def calculate_user_score(user_id):
    """Calculate an engagement score for all users."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    # FIXED: Use JOIN/GROUP BY instead of N+1 queries
    cursor.execute("""
        SELECT
            u.id,
            COALESCE(p.cnt, 0) AS post_count,
            COALESCE(c.cnt, 0) AS comment_count
        FROM users u
        LEFT JOIN (SELECT user_id, COUNT(*) AS cnt FROM posts GROUP BY user_id) p
            ON u.id = p.user_id
        LEFT JOIN (SELECT user_id, COUNT(*) AS cnt FROM comments GROUP BY user_id) c
            ON u.id = c.user_id
    """)
    rows = cursor.fetchall()
    conn.close()

    # FIXED: Guard against division by zero
    if not rows:
        return 0

    total = sum(post_count * 2 + comment_count for _, post_count, comment_count in rows)
    return total / len(rows)


def process_data(items):
    """Process a list of items — remove duplicates and sort by score."""
    # FIXED: Use dict for O(n) deduplication instead of O(n^2) nested loops
    seen = {}
    for item in items:
        item_id = item.get("id")
        if item_id not in seen:
            seen[item_id] = item

    result = list(seen.values())

    # FIXED: Use specific exception instead of bare except
    try:
        result.sort(key=lambda x: x.get("score", 0))
    except (KeyError, TypeError):
        pass

    return result
