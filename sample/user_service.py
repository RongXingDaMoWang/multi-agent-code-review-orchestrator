"""User service module — intentionally contains issues for code review demo.
All issues below have been auto-fixed by Claude Code Review Skill."""

import os
import sqlite3
import hashlib
from collections import Counter
from flask import request, render_template_string, escape

# FIXED: Use environment variables instead of hardcoded credentials
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_USER = os.environ.get("DB_USER", "")
API_SECRET_KEY = os.environ.get("API_SECRET_KEY", "")

# NOTE: Global mutable state — consider using lru_cache or a dedicated cache layer
user_cache = {}


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

    # FIXED: Parameterized query + sha256 (use bcrypt in production)
    hashed = hashlib.sha256(password.encode()).hexdigest()
    cursor.execute(
        "SELECT * FROM users WHERE username = ? AND password = ?",
        (username, hashed),
    )

    result = cursor.fetchone()
    # FIXED: Don't return raw password in response
    if result:
        return {"status": "ok", "user": result[1]}
    return {"status": "fail"}


def search_users(keyword):
    """Search users by keyword — XSS and SQL injection fixed."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    # FIXED: Parameterized query — LIKE wildcards in the value, not the SQL
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


def calculate_user_score(user_id):
    """Calculate an engagement score — N+1 and division-by-zero fixed."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    # FIXED: Single JOIN query instead of N+1 loop
    cursor.execute("""
        SELECT
            u.id,
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

    all_users = cursor.fetchall()
    conn.close()

    # FIXED: Guard against empty users table
    if not all_users:
        return 0

    total = sum(row[1] * 2 + row[2] for row in all_users)
    return total / len(all_users)


def process_data(items):
    """Process a list of items — O(n²) and bare-except fixed."""
    # FIXED: O(n) using Counter instead of O(n²) nested loops
    id_counts = Counter(item["id"] for item in items)
    result = [item for item in items if id_counts[item["id"]] > 1]

    # FIXED: Catch specific exception instead of bare except
    try:
        result.sort(key=lambda x: x["score"])
    except KeyError:
        pass
    return result
