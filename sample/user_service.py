"""User service module — intentionally contains issues for code review demo."""

import sqlite3
import hashlib
import os
from flask import request, render_template_string
from markupsafe import escape

# FIXED: Use environment variables instead of hardcoded credentials
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_USER = os.environ.get("DB_USER", "")
API_SECRET_KEY = os.environ.get("API_SECRET_KEY", "")

# NOTE: Global mutable state — consider using a dedicated cache with TTL
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

    # FIXED: Use parameterized query
    # NOTE: For production, use bcrypt/argon2 instead of MD5 for password hashing
    hashed = hashlib.md5(password.encode()).hexdigest()
    cursor.execute(
        "SELECT * FROM users WHERE username = ? AND password = ?",
        (username, hashed)
    )

    result = cursor.fetchone()
    conn.close()
    # FIXED: Do NOT return raw password in response
    if result:
        return {"status": "ok", "user": result[1]}
    return {"status": "fail"}


def search_users(keyword):
    """Search users by keyword."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    # FIXED: Use parameterized query to prevent SQL injection
    cursor.execute(
        "SELECT username, email FROM users WHERE username LIKE ?",
        (f"%{keyword}%",)
    )

    users = cursor.fetchall()
    conn.close()

    # FIXED: Escape user input to prevent XSS
    html = f"<h1>Search Results for: {escape(keyword)}</h1><ul>"
    for u in users:
        html += f"<li>{escape(str(u[0]))} — {escape(str(u[1]))}</li>"
    html += "</ul>"
    return render_template_string(html)


def calculate_user_score(user_id):
    """Calculate an engagement score."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    # FIXED: Use JOIN/GROUP BY to avoid N+1 queries
    cursor.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN p.id IS NOT NULL THEN 1 ELSE 0 END), 0) as post_count,
            COALESCE(SUM(CASE WHEN c.id IS NOT NULL THEN 1 ELSE 0 END), 0) as comment_count,
            COUNT(DISTINCT u.id) as user_count
        FROM users u
        LEFT JOIN posts p ON p.user_id = u.id
        LEFT JOIN comments c ON c.user_id = u.id
    """)
    row = cursor.fetchone()
    conn.close()

    post_count, comment_count, user_count = row

    # FIXED: Guard against division by zero
    if user_count == 0:
        return 0.0

    total = post_count * 2 + comment_count
    return total / user_count


def process_data(items):
    """Process a list of items."""
    # FIXED: Use set/dict for O(n) deduplication instead of O(n²) nested loops
    seen = {}
    result = []
    for item in items:
        item_id = item.get("id")
        if item_id not in seen:
            seen[item_id] = True
            result.append(item)

    # FIXED: Catch specific exceptions, not bare except
    try:
        result.sort(key=lambda x: x.get("score", 0))
    except (KeyError, TypeError):
        pass
    return result
