"""User service module — fixed version with all security and quality improvements."""

import os
import sqlite3
import bcrypt
from functools import lru_cache
from markupsafe import escape
from flask import render_template_string
from dotenv import load_dotenv

load_dotenv()

# FIXED: Use environment variables for credentials
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_USER = os.environ.get("DB_USER", "")
API_SECRET_KEY = os.environ.get("API_SECRET_KEY", "")


@lru_cache(maxsize=128)
def get_user_by_id(user_id):
    """Get user from database by ID."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    # FIXED: Parameterized query prevents SQL injection
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))

    user = cursor.fetchone()
    conn.close()
    return user


def login(username, password):
    """Authenticate a user."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    # FIXED: Parameterized query + bcrypt verification (NOT MD5)
    cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
    result = cursor.fetchone()
    conn.close()

    if result and bcrypt.checkpw(password.encode(), result[2].encode()):
        # FIXED: Never return the raw password
        return {"status": "ok", "user": result[1]}
    return {"status": "fail"}


def search_users(keyword):
    """Search users by keyword."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    # FIXED: Parameterized LIKE query prevents SQL injection
    cursor.execute(
        "SELECT username, email FROM users WHERE username LIKE ?",
        (f"%{keyword}%",),
    )

    users = cursor.fetchall()
    conn.close()

    # FIXED: Escape user input to prevent XSS
    html = f"<h1>Search Results for: {escape(keyword)}</h1><ul>"
    for u in users:
        html += f"<li>{escape(u[0])} — {escape(u[1])}</li>"
    html += "</ul>"
    return render_template_string(html)


def calculate_user_score():
    """Calculate an engagement score — FIXED: single query with JOIN + GROUP BY."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    # FIXED: Single query replaces N+1 pattern
    cursor.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN p.user_id IS NOT NULL THEN 2 ELSE 0 END), 0) +
            COALESCE(SUM(CASE WHEN c.user_id IS NOT NULL THEN 1 ELSE 0 END), 0) AS total_score,
            COUNT(DISTINCT u.id) AS user_count
        FROM users u
        LEFT JOIN posts p ON u.id = p.user_id
        LEFT JOIN comments c ON u.id = c.user_id
    """)
    total_score, user_count = cursor.fetchone()
    conn.close()

    # FIXED: Guard against division by zero
    if user_count == 0:
        return 0
    return total_score / user_count


def process_data(items):
    """Process a list of items — FIXED: O(n) with dict lookup."""
    seen = {}
    result = []
    # FIXED: O(n) with dict instead of O(n²) nested loops
    for item in items:
        item_id = item["id"]
        if item_id in seen:
            result.append(item)
        else:
            seen[item_id] = item

    # FIXED: Catch specific exception, not bare except
    try:
        result.sort(key=lambda x: x["score"])
    except KeyError:
        # Some items missing "score" field — skip sorting
        pass
    return result
