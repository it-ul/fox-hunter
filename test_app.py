# -*- coding: utf-8 -*-
"""Проверка «Охота на лис» через Flask test client.

Покрытие:
- Авторизация: регистрация (уникальность логина, валидация, хэш пароля),
  вход, выход, доступ к игре только после входа.
- Игра: меню, старт, пеленг, серые линии, победа, валидация ввода.

Запуск: python test_app.py
Выход: 0 — всё зелёное, 1 — есть падения.
"""

import os
import sqlite3
import sys
import tempfile
from unittest.mock import patch

from werkzeug.security import check_password_hash

import app as app_module

# --- Тестовая БД: временный файл, отдельный от настоящей users.db ---------
_tmp = tempfile.mkdtemp(prefix="foxhunter_tests_")
app_module.app.config["DATABASE"] = os.path.join(_tmp, "test_users.db")
app_module.init_db(force=True)


def _register(client, username, password):
    """Регистрирует пользователя через форму."""
    return client.post("/register", data={"username": username, "password": password})


def _login(client, username, password):
    """Входит под пользователем."""
    return client.post("/login", data={"username": username, "password": password})


def _auth_client(username, password="secret123"):
    """Возвращает клиент с зарегистрированным и вошедшим игроком."""
    client = app_module.app.test_client()
    _register(client, username, password)
    resp = _login(client, username, password)
    assert resp.status_code == 302, "login должен редиректить на /"
    return client


# --- Авторизация ----------------------------------------------------------

def test_guest_sees_welcome():
    """Гость видит приветствие с входом/регистрацией, но не игру."""
    client = app_module.app.test_client()
    body = client.get("/").get_data(as_text=True)
    assert "Регистрация" in body
    assert "Вход" in body
    assert "Новая игра" not in body  # меню игры гостю не показываем


def test_register_success_and_login():
    """Регистрация создаёт игрока, после входа открывается меню игры."""
    client = app_module.app.test_client()
    resp = _register(client, "alice", "secret123")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
    resp = _login(client, "alice", "secret123")
    assert resp.status_code == 302
    body = client.get("/").get_data(as_text=True)
    assert "Новая игра" in body
    assert "alice" in body  # имя игрока на странице


def test_register_duplicate_username():
    """Повторная регистрация с тем же логином отклоняется."""
    client = app_module.app.test_client()
    _register(client, "bob", "secret123")
    resp = _register(client, "bob", "other456")
    assert resp.status_code == 200  # форма с ошибкой, без редиректа
    body = resp.get_data(as_text=True)
    assert "уже занят" in body


def test_register_case_insensitive():
    """Логины не различают регистр: 'Carol' и 'carol' — одно и то же имя."""
    client = app_module.app.test_client()
    _register(client, "Carol", "secret123")
    resp = _register(client, "carol", "secret123")
    assert resp.status_code == 200
    assert "уже занят" in resp.get_data(as_text=True)


def test_register_validation():
    """Короткий логин и короткий пароль отклоняются с сообщением."""
    client = app_module.app.test_client()
    resp = _register(client, "ab", "secret123")
    assert "Логин должен быть" in resp.get_data(as_text=True)
    resp = _register(client, "validname", "123")
    assert "Пароль должен быть" in resp.get_data(as_text=True)


def test_login_wrong_password():
    """Неверный пароль не пускает в игру."""
    client = app_module.app.test_client()
    _register(client, "dave", "secret123")
    resp = _login(client, "dave", "wrong999")
    assert resp.status_code == 200
    assert "Неверный логин или пароль" in resp.get_data(as_text=True)
    body = client.get("/").get_data(as_text=True)
    assert "Новая игра" not in body


def test_login_unknown_user():
    """Несуществующий логин отклоняется."""
    client = app_module.app.test_client()
    resp = _login(client, "nobody", "secret123")
    assert resp.status_code == 200
    assert "Неверный логин или пароль" in resp.get_data(as_text=True)


def test_password_stored_hashed():
    """Пароль в БД хранится хэшем, а не открытым текстом."""
    client = app_module.app.test_client()
    _register(client, "erin", "secret123")
    conn = sqlite3.connect(app_module.app.config["DATABASE"])
    try:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE username = ?", ("erin",)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    stored = row[0]
    assert stored != "secret123"  # не открытый текст
    assert check_password_hash(stored, "secret123")  # но проверяется верно
    assert not check_password_hash(stored, "wrong999")


def test_game_requires_login():
    """Запуск игры без входа уводит на страницу входа."""
    client = app_module.app.test_client()
    resp = client.post("/start", data={"size": "9", "foxes": "5"})
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
    resp = client.post("/move", data={"row": "0", "col": "0"})
    assert "/login" in resp.headers["Location"]


def test_logout_returns_to_welcome():
    """После выхода игра очищается, гость снова видит приветствие."""
    client = _auth_client("frank")
    client.post("/start", data={"size": "8", "foxes": "5"})
    resp = client.post("/logout")
    assert resp.status_code == 302
    body = client.get("/").get_data(as_text=True)
    assert "Регистрация" in body
    assert "Новая игра" not in body


# --- Игра (только после входа) -------------------------------------------

def test_index_menu():
    """Авторизованный игрок видит меню новой игры."""
    client = _auth_client("grace")
    body = client.get("/").get_data(as_text=True)
    assert "Охота на лис" in body
    assert "Новая игра" in body
    assert "Размер поля" in body


def test_start_game():
    """Старт создаёт игру 9x9 и рисует поле."""
    client = _auth_client("heidi")
    resp = client.post("/start", data={"size": "9", "foxes": "5"})
    assert resp.status_code == 302  # редирект на /
    body = client.get("/").get_data(as_text=True)
    assert "Ходы: 0 / 100" in body
    assert "Лисы найдены: 0 / 5" in body
    # 81 закрытая клетка на поле 9x9
    assert body.count('class="cell hidden') == 81


def test_bearing_logic():
    """Пеленг считается по вертикали/горизонтали/диагоналям, лисы помечаются."""
    client = _auth_client("ivan")
    # Подменяем случайную расстановку: 5 лис в первой строке
    foxes = [(0, 0), (0, 1), (0, 2), (0, 3), (0, 4)]
    with patch("app.random.sample", return_value=foxes):
        client.post("/start", data={"size": "8", "foxes": "5"})

    # Ход в лису (0,0): все 5 лис на строке 0 -> пеленг 5, лиса найдена
    client.post("/move", data={"row": "0", "col": "0"})
    body = client.get("/").get_data(as_text=True)
    assert "🦊" in body
    assert "Лисы найдены: 1 / 5" in body
    assert "Ходы: 1 / 100" in body

    # Ход в (1,1): лисы на той же вертикали/диагоналях: (0,1),(0,0),(0,2) -> пеленг 3
    client.post("/move", data={"row": "1", "col": "1"})
    body = client.get("/").get_data(as_text=True)
    assert ">3<" in body
    assert "Ходы: 2 / 100" in body

    # Ход в (7,6): пеленг 0 — вертикаль, горизонталь и диагонали клетки серые
    client.post("/move", data={"row": "7", "col": "6"})
    body = client.get("/").get_data(as_text=True)
    assert ">0<" in body
    # открытая нулевая клетка и закрытая клетка на той же строке — серые
    assert 'class="cell number grey-line ' in body
    assert 'class="cell hidden grey-line ' in body
    # клетка вне линий (1,1) серой не стала
    assert ">3<" in body
    assert 'grey-line ">3<' not in body


def test_win_condition():
    """Поиск всех лис завершает игру победой."""
    client = _auth_client("judy")
    foxes = [(0, 0), (0, 1)]
    with patch("app.random.sample", return_value=foxes):
        client.post("/start", data={"size": "8", "foxes": "2"})
    for r, c in foxes:
        client.post("/move", data={"row": str(r), "col": str(c)})
    body = client.get("/").get_data(as_text=True)
    assert "Поздравляем" in body
    assert "Все 2 лис найдены за 2 ходов" in body
    assert 'class="message win"' in body


def test_invalid_move_ignored():
    """Ход за пределы поля не меняет состояние игры."""
    client = _auth_client("ken")
    client.post("/start", data={"size": "8", "foxes": "5"})
    client.post("/move", data={"row": "99", "col": "99"})
    body = client.get("/").get_data(as_text=True)
    assert "Ходы: 0 / 100" in body


def test_size_clamp():
    """Некорректный размер поля приводится к 9."""
    client = _auth_client("laura")
    client.post("/start", data={"size": "999", "foxes": "5"})
    body = client.get("/").get_data(as_text=True)
    assert body.count('class="cell hidden') == 81


def test_size_15():
    """Поле 15x15 доступно и использует мелкие клетки."""
    client = _auth_client("mike")
    resp = client.post("/start", data={"size": "15", "foxes": "10"})
    assert resp.status_code == 302
    body = client.get("/").get_data(as_text=True)
    assert body.count('class="cell hidden') == 225
    assert 'cell hidden  small' in body


def test_exit_game():
    """Выход возвращает в стартовое меню."""
    client = _auth_client("nina")
    client.post("/start", data={"size": "8", "foxes": "5"})
    client.post("/exit")
    body = client.get("/").get_data(as_text=True)
    assert "Новая игра" in body


def test_save_and_load_roundtrip():
    """Сохранив игру, можно начать новую и вернуться к сохранённой."""
    client = _auth_client("olga")
    foxes = [(0, 0), (0, 1)]
    with patch("app.random.sample", return_value=foxes):
        client.post("/start", data={"size": "8", "foxes": "2"})
    client.post("/move", data={"row": "0", "col": "0"})  # лиса найдена, ход 1
    resp = client.post("/save")
    assert resp.status_code == 302
    assert "Игра сохранена" in client.get("/").get_data(as_text=True)

    # Начинаем новую игру — старое состояние ушло
    client.post("/exit")
    with patch("app.random.sample", return_value=[(3, 3), (4, 4)]):
        client.post("/start", data={"size": "8", "foxes": "2"})
    body = client.get("/").get_data(as_text=True)
    assert "Ходы: 0 / 100" in body

    # Загружаем сохранение — состояние вернулось (1 ход, 1 лиса)
    client.post("/load")
    body = client.get("/").get_data(as_text=True)
    assert "Игра загружена" in body
    assert "Ходы: 1 / 100" in body
    assert "Лисы найдены: 1 / 2" in body

    # Из загруженной игры можно продолжать ходить
    with patch("app.random.sample"):
        client.post("/move", data={"row": "0", "col": "1"})
    body = client.get("/").get_data(as_text=True)
    assert "Поздравляем" in body
    assert "Все 2 лис найдены за 2 ходов" in body


def test_save_is_one_slot_per_player():
    """Повторное сохранение обновляет слот, а не плодит записи."""
    client = _auth_client("peter")
    foxes = [(0, 0)]
    with patch("app.random.sample", return_value=foxes):
        client.post("/start", data={"size": "8", "foxes": "1"})
    client.post("/save")
    client.post("/move", data={"row": "1", "col": "1"})
    client.post("/save")
    conn = sqlite3.connect(app_module.app.config["DATABASE"])
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM saves WHERE user_id = "
            "(SELECT id FROM users WHERE username = 'peter')"
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1  # один слот на игрока, вторая запись обновила первую


def test_saves_isolated_between_players():
    """Сохранение одного игрока не видно другому."""
    alice = _auth_client("rose")
    foxes = [(0, 0)]
    with patch("app.random.sample", return_value=foxes):
        alice.post("/start", data={"size": "8", "foxes": "1"})
    alice.post("/save")

    bob = _auth_client("sam")
    body = bob.get("/").get_data(as_text=True)
    assert "Продолжить сохранённую игру" not in body  # у bob нет сохранения
    bob.post("/load")
    assert "нет сохранённой игры" in bob.get("/").get_data(as_text=True)

    # у alice сохранение на месте: выходим в меню — там кнопка продолжения
    alice.post("/exit")
    body = alice.get("/").get_data(as_text=True)
    assert "Продолжить сохранённую игру" in body


def test_save_requires_active_game():
    """Без идущей игры сохранить нельзя."""
    client = _auth_client("tom")
    resp = client.post("/save")
    assert resp.status_code == 302
    assert "Сохранять можно только идущую" in client.get("/").get_data(as_text=True)


def main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {t.__name__}: {e!r}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
