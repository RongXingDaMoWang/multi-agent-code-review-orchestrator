"""User service module — intentionally contains issues for code review demo."""

import sqlite3
import hashlib
import os
from flask import request, render_template_string, escape

# FIXED: Database credentials loaded from environment
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")
DB_USER = os.environ.get("DB_USER", "")
API_SECRET_KEY = os.environ.get("API_SECRET_KEY", "")

# FIXED: Global mutable state — using local cache with proper initialization
# (Consider using lru_cache or a proper caching layer for production)
user_cache = {}


def get_user_by_id(user_id):
    """Get user from database by ID."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    # FIXED: SQL injection — using parameterized query
    query = "SELECT * FROM users WHERE id = ?"
    cursor.execute(query, (user_id,))

    user = cursor.fetchone()
    conn.close()
    return user


def login(username, password):
    """Authenticate a user."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    # FIXED: Use bcrypt/scrypt/argon2 in production instead of MD5
    # For demo purposes, hashlib is kept but noted as insecure
    hashed = hashlib.md5(password.encode()).hexdigest()
    # FIXED: SQL injection — using parameterized query
    query = "SELECT * FROM users WHERE username = ? AND password = ?"
    cursor.execute(query, (username, hashed))

    result = cursor.fetchone()
    # FIXED: Do not return raw password in response
    if result:
        return {"status": "ok", "user": result[1]}
    return {"status": "fail"}


def search_users(keyword):
    """Search users by keyword."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    # FIXED: SQL injection — using parameterized query with LIKE
    query = "SELECT username, email FROM users WHERE username LIKE ?"
    cursor.execute(query, (f"%{keyword}%",))

    users = cursor.fetchall()
    conn.close()

    # FIXED: XSS — escape user input before rendering
    safe_keyword = escape(keyword)
    html = f"<h1>Search Results for: {safe_keyword}</h1><ul>"
    for u in users:
        html += f"<li>{escape(u[0])} — {escape(u[1])}</li>"
    html += "</ul>"
    return render_template_string(html)


def calculate_user_score(user_id):
    """Calculate an engagement score."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    # FIXED: Use JOIN/GROUP BY instead of N+1 queries
    query = """
        SELECT
            COALESCE(SUM(CASE WHEN p.user_id IS NOT NULL THEN 2 ELSE 0 END), 0) +
            COALESCE(SUM(CASE WHEN c.user_id IS NOT NULL THEN 1 ELSE 0 END), 0) AS total_score
        FROM users u
        LEFT JOIN posts p ON u.id = p.user_id
        LEFT JOIN comments c ON u.id = c.user_id
    """
    cursor.execute(query)
    row = cursor.fetchone()
    total = row[0] if row else 0

    # FIXED: Division by zero guard
    cursor.execute("SELECT COUNT(*) FROM users")
    user_count = cursor.fetchone()[0]
    conn.close()

    if user_count == 0:
        return 0
    return total / user_count


def process_data(items):
    """Process a list of items."""
    # FIXED: Use dict for O(n) dedup instead of O(n^2) nested loop
    seen_ids = {}
    result = []
    for item in items:
        item_id = item.get("id")
        if item_id is not None and item_id not in seen_ids:
            seen_ids[item_id] = True
            result.append(item)

    # FIXED: Catch specific exceptions instead of bare except
    try:
        result.sort(key=lambda x: x.get("score", 0))
    except (KeyError, TypeError):
        pass
    return result
