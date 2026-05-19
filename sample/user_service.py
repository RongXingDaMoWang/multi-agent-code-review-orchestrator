"""User service module — intentionally contains issues for code review demo."""

import sqlite3
import hashlib
from flask import request, render_template_string

# BUG: Hardcoded database credentials
DB_PASSWORD = "admin123"
DB_USER = "root"
API_SECRET_KEY = "sk-live-abc123def456ghi789jkl"

# BUG: Global mutable state
user_cache = {}


def get_user_by_id(user_id):
    """Get user from database by ID."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    # BUG: SQL injection — raw string formatting in SQL query
    query = f"SELECT * FROM users WHERE id = {user_id}"
    cursor.execute(query)

    user = cursor.fetchone()
    conn.close()
    return user


def login(username, password):
    """Authenticate a user."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    # BUG: SQL injection + insecure password hashing (MD5)
    hashed = hashlib.md5(password.encode()).hexdigest()
    query = f"SELECT * FROM users WHERE username = '{username}' AND password = '{hashed}'"
    cursor.execute(query)

    result = cursor.fetchone()
    # BUG: Returns raw password in response
    if result:
        return {"status": "ok", "user": result[1], "password": password}
    return {"status": "fail"}


def search_users(keyword):
    """Search users by keyword — SECURITY: XSS vulnerability."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    # BUG: SQL injection in LIKE clause
    query = f"SELECT username, email FROM users WHERE username LIKE '%{keyword}%'"
    cursor.execute(query)

    users = cursor.fetchall()
    conn.close()

    # BUG: XSS — unescaped user input rendered directly into HTML
    html = f"<h1>Search Results for: {keyword}</h1><ul>"
    for u in users:
        html += f"<li>{u[0]} — {u[1]}</li>"
    html += "</ul>"
    return render_template_string(html)


def calculate_user_score(user_id):
    """Calculate an engagement score — PERFORMANCE issue."""
    conn = sqlite3.connect("users.db")
    cursor = conn.cursor()

    # BUG: N+1 query pattern — loops inside a query result
    cursor.execute("SELECT id FROM users")
    all_users = cursor.fetchall()
    total = 0

    for uid in all_users:
        # Fetches data per user in a loop instead of using JOIN/GROUP BY
        cursor.execute(f"SELECT COUNT(*) FROM posts WHERE user_id = {uid[0]}")
        post_count = cursor.fetchone()[0]
        cursor.execute(f"SELECT COUNT(*) FROM comments WHERE user_id = {uid[0]}")
        comment_count = cursor.fetchone()[0]
        total += post_count * 2 + comment_count

    conn.close()
    # BUG: Division by zero if no users
    return total / len(all_users)


def process_data(items):
    """Process a list of items — CODE SMELL: overly complex."""
    result = []
    # BUG: Unnecessary nested loops — O(n^2) when O(n) would work
    for i in range(len(items)):
        for j in range(len(items)):
            if i != j and items[i]["id"] == items[j]["id"]:
                result.append(items[i])
    # BUG: Bare except swallows all exceptions
    try:
        result.sort(key=lambda x: x["score"])
    except:
        pass
    return result
