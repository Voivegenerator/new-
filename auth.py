import sqlite3
from pathlib import Path

from flask import Blueprint, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

auth_bp = Blueprint('auth', __name__)

DB_PATH = Path(__file__).parent / 'users.db'


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute(
        '''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )'''
    )
    conn.commit()
    conn.close()


@auth_bp.get('/login')
def login():
    if session.get('user_id'):
        return redirect(url_for('home'))
    return render_template('pages/login.html')


@auth_bp.post('/login')
def login_post():
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')
    conn = get_db()
    user = conn.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
    conn.close()
    if user and check_password_hash(user['password_hash'], password):
        session['user_id'] = user['id']
        session['user_name'] = user['name']
        next_url = request.form.get('next') or url_for('home')
        return redirect(next_url)
    flash('ईमेल या पासवर्ड गलत है।')
    return render_template('pages/login.html', email=email), 401


@auth_bp.get('/signup')
def signup():
    if session.get('user_id'):
        return redirect(url_for('home'))
    return render_template('pages/signup.html')


@auth_bp.post('/signup')
def signup_post():
    name = request.form.get('name', '').strip()
    email = request.form.get('email', '').strip().lower()
    password = request.form.get('password', '')

    if not name or not email or '@' not in email:
        flash('कृपया सही नाम और ईमेल डालें।')
        return render_template('pages/signup.html', name=name, email=email), 400
    if len(password) < 6:
        flash('पासवर्ड कम से कम 6 अक्षर का होना चाहिए।')
        return render_template('pages/signup.html', name=name, email=email), 400

    conn = get_db()
    existing = conn.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()
    if existing:
        conn.close()
        flash('यह ईमेल पहले से रजिस्टर्ड है। कृपया लॉगिन करें।')
        return redirect(url_for('auth.login'))

    pw_hash = generate_password_hash(password)
    conn.execute(
        'INSERT INTO users (name, email, password_hash) VALUES (?, ?, ?)',
        (name, email, pw_hash),
    )
    conn.commit()
    user = conn.execute('SELECT id, name FROM users WHERE email=?', (email,)).fetchone()
    conn.close()

    session['user_id'] = user['id']
    session['user_name'] = user['name']
    return redirect(url_for('home'))


@auth_bp.get('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))
