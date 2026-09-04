# -*- coding: utf-8 -*-
"""
Игра «Охота на лис» (Fox Hunter) — веб-приложение на Flask.

Правила:
- Поле 8x8, 9x9, 10x10 или 15x15 (по умолчанию 9x9).
- Лисы расставляются случайно, в одной клетке не бывает двух лис.
- Количество лис — от 5 до размера поля (по умолчанию 5, для 15x15 — 10).
- Лисы не видны игроку. Лимит — 100 ходов на игру.
- Клик по клетке показывает «Пеленг» — число лис на той же вертикали,
  горизонтали и обеих диагоналях выбранной клетки.
- Если в клетке лиса — она показывается и помечается найденной, но
  продолжает учитываться в пеленге других клеток.
- Конец игры: найдены все лисы (победа) или исчерпан лимит ходов (поражение).

Запуск:
    pip install flask
    python app.py
    → открыть http://127.0.0.1:5000
"""

import os
import random
import sqlite3
from functools import wraps

from flask import (
    Flask,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
# Секретный ключ нужен для сессии (состояния игры и авторизации).
# В проде задаётся переменной окружения SECRET_KEY.
app.secret_key = os.environ.get("SECRET_KEY", "fox-hunter-secret-key")

# --- База данных пользователей (SQLite) ---------------------------------

DB_FILENAME = "users.db"


def _db_path():
    """Путь к файлу БД: из конфига Flask (для тестов) или рядом с app.py."""
    return app.config.get("DATABASE") or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), DB_FILENAME
    )


def init_db(force=False):
    """Создаёт таблицу пользователей. force=True — пересоздать с нуля."""
    path = _db_path()
    if force and os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()
    conn.close()


init_db()  # гарантируем наличие БД при старте


def _db():
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def _user_by_username(username):
    """Ищет пользователя по логину (без учёта регистра)."""
    conn = _db()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE username = ? COLLATE NOCASE",
            (username,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _create_user(username, password_hash):
    """Добавляет пользователя. Кидает sqlite3.IntegrityError, если логин занят."""
    conn = _db()
    try:
        conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, password_hash),
        )
        conn.commit()
    finally:
        conn.close()


def login_required(view):
    """Защищает маршрут: доступен только вошедшему игроку."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


# Доступные размеры поля
FIELD_SIZES = [8, 9, 10, 15]
# Количество лис по умолчанию для каждого размера
DEFAULT_FOXES = {8: 5, 9: 5, 10: 5, 15: 10}
# Лимит ходов на одну игру
MAX_MOVES = 100


def _key(r, c):
    """Строковый ключ клетки вида 'r,c'.

    Используем строки вместо кортежей как ключи словаря revealed:
    Flask хранит сессию в JSON, а JSON не поддерживает кортежи-ключи.
    """
    return f"{r},{c}"


def _load_game():
    """Достаёт игру из сессии и приводит данные к удобному виду.

    В JSON кортежи превращаются в списки, а множества не поддерживаются,
    поэтому здесь мы это «чиним» после чтения из сессии.
    """
    game = session.get("game")
    if not game:
        return None
    game["foxes"] = [tuple(f) for f in game["foxes"]]  # [[r, c], ...] -> [(r, c), ...]
    game["found_cells"] = set(game["found_cells"])  # список строк -> множество
    return game


def _save_game(game):
    """Сохраняет игру в сессию в JSON-совместимом виде."""
    session["game"] = {
        "size": game["size"],
        "foxes": game["foxes"],  # кортежи сами станут списками в JSON
        "moves": game["moves"],
        "found": game["found"],
        "revealed": game["revealed"],  # ключи уже строки "r,c" — ок
        "found_cells": list(game["found_cells"]),
        "status": game["status"],  # playing | win | lose
        "message": game["message"],
    }


def _compute_grey_lines(game):
    """Собирает линии открытых нулевых клеток для серой подсветки.

    Если пеленг клетки равен 0, на её вертикали, горизонтали и обеих
    диагоналях нет ни одной лисы — такие линии закрашиваются серым,
    чтобы игрок видел безопасные зоны.
    """
    rows, cols, diag_main, diag_anti = set(), set(), set(), set()
    for key, bearing in game["revealed"].items():
        if bearing == 0:
            r, c = map(int, key.split(","))
            rows.add(r)
            cols.add(c)
            diag_main.add(r - c)  # диагональ "\"
            diag_anti.add(r + c)  # диагональ "/"
    return {
        "rows": rows,
        "cols": cols,
        "diag_main": diag_main,
        "diag_anti": diag_anti,
    }


@app.route("/")
def index():
    """Главная: для гостя — вход/регистрация, для игрока — меню игры или поле."""
    if "user_id" not in session:
        return render_template("welcome.html")
    game = _load_game()
    grey_lines = _compute_grey_lines(game) if game else None
    return render_template(
        "index.html",
        field_sizes=FIELD_SIZES,
        default_foxes=DEFAULT_FOXES,
        max_moves=MAX_MOVES,
        game=game,
        grey_lines=grey_lines,
        username=session.get("username"),
    )


@app.route("/register", methods=["GET", "POST"])
def register():
    """Регистрация игрока: уникальный логин + пароль (хранится хэшем)."""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        error = None
        if len(username) < 3 or len(username) > 30:
            error = "Логин должен быть от 3 до 30 символов."
        elif len(password) < 6:
            error = "Пароль должен быть не короче 6 символов."
        elif _user_by_username(username):
            error = "Этот логин уже занят. Выберите другой."
        if error:
            flash(error, "error")
        else:
            try:
                _create_user(username, generate_password_hash(password))
            except sqlite3.IntegrityError:
                # защита от гонки: кто-то успел занять логин
                flash("Этот логин уже занят. Выберите другой.", "error")
            else:
                flash("Регистрация прошла успешно. Теперь войдите.", "ok")
                return redirect(url_for("login"))
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    """Вход игрока: проверка логина и пароля."""
    if request.method == "POST":
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        user = _user_by_username(username) if username else None
        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("index"))
        flash("Неверный логин или пароль.", "error")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    """Выход игрока. Сессия игры очищается (она привязана к игроку)."""
    session.pop("user_id", None)
    session.pop("username", None)
    session.pop("game", None)
    return redirect(url_for("index"))


@app.route("/start", methods=["POST"])
@login_required
def start():
    """Создаёт новую игру: поле + случайная расстановка лис."""
    # Читаем параметры формы (с запасом на кривой ввод)
    try:
        size = int(request.form.get("size", 9))
        foxes_count = int(request.form.get("foxes", DEFAULT_FOXES.get(size, 5)))
    except ValueError:
        size, foxes_count = 9, 5

    if size not in FIELD_SIZES:
        size = 9
    # По правилам: количество лис — от 5 до размера поля (сложность игры)
    foxes_count = max(5, min(foxes_count, size))

    # random.sample выбирает уникальные клетки, поэтому две лисы
    # гарантированно не попадут в одну клетку
    all_cells = [(r, c) for r in range(size) for c in range(size)]
    foxes = random.sample(all_cells, foxes_count)

    _save_game(
        {
            "size": size,
            "foxes": foxes,
            "moves": 0,  # сделано ходов
            "found": 0,  # найдено лис
            "revealed": {},  # {"r,c": пеленг} — открытые клетки
            "found_cells": set(),  # ключи клеток с найденными лисами
            "status": "playing",
            "message": None,
        }
    )
    return redirect(url_for("index"))


@app.route("/move", methods=["POST"])
@login_required
def move():
    """Ход игрока: клик по клетке поля."""
    game = _load_game()
    # Ход можно сделать, только пока идёт игра
    if not game or game["status"] != "playing":
        return redirect(url_for("index"))

    try:
        row = int(request.form.get("row", -1))
        col = int(request.form.get("col", -1))
    except ValueError:
        return redirect(url_for("index"))

    key = _key(row, col)
    size = game["size"]

    # Клетка должна быть внутри поля и ещё не открытой
    if not (0 <= row < size and 0 <= col < size) or key in game["revealed"]:
        return redirect(url_for("index"))

    # --- Подсчёт Пеленга выбранной клетки ---
    # Лиса учитывается, если она на той же:
    #   горизонтали (fr == row), вертикали (fc == col),
    #   диагонали "\" (fr - fc == row - col) или диагонали "/" (fr + fc == row + col).
    # Лиса в самой выбранной клетке тоже попадает на её вертикаль/горизонталь,
    # поэтому учитывается в пеленге (легко исключить, добавив and (fr, fc) != (row, col)).
    bearing = 0
    for fr, fc in game["foxes"]:
        if fr == row or fc == col or fr - fc == row - col or fr + fc == row + col:
            bearing += 1

    # Открываем клетку и записываем пеленг
    game["revealed"][key] = bearing
    game["moves"] += 1

    # --- Попали в лису? ---
    if (row, col) in game["foxes"]:
        game["found_cells"].add(key)
        game["found"] += 1

    # --- Проверяем условия окончания игры ---
    if game["found"] == len(game["foxes"]):
        # Найдены все лисы — победа
        game["status"] = "win"
        game["message"] = (
            f"Поздравляем! Все {game['found']} лис найдены за {game['moves']} ходов."
        )
    elif game["moves"] >= MAX_MOVES:
        # Лимит ходов исчерпан, а лисы не все найдены — поражение
        game["status"] = "lose"
        game["message"] = (
            f"Лимит ходов ({MAX_MOVES}) исчерпан. "
            f"Найдено {game['found']} из {len(game['foxes'])} лис."
        )

    _save_game(game)
    return redirect(url_for("index"))


@app.route("/exit", methods=["POST"], endpoint="exit")
@login_required
def exit_game():
    """Выход из текущей игры в начало."""
    session.pop("game", None)
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True)
