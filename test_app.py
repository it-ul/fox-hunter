# -*- coding: utf-8 -*-
"""Проверка работоспособности «Охота на лис» через Flask test client.

Запуск: python test_app.py
Выход: 0 — всё зелёное, 1 — есть падения.
"""

import sys
from unittest.mock import patch

import app as app_module


def test_index_menu():
    """Главная страница отдаёт стартовое меню."""
    client = app_module.app.test_client()
    resp = client.get("/")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Охота на лис" in body
    assert "Новая игра" in body
    assert "Размер поля" in body


def test_start_game():
    """Старт создаёт игру 9x9 и рисует поле."""
    client = app_module.app.test_client()
    resp = client.post("/start", data={"size": "9", "foxes": "5"})
    assert resp.status_code == 302  # редирект на /
    body = client.get("/").get_data(as_text=True)
    assert "Ходы: 0 / 100" in body
    assert "Лисы найдены: 0 / 5" in body
    # 81 закрытая клетка на поле 9x9
    assert body.count('class="cell hidden') == 81


def test_bearing_logic():
    """Пеленг считается по вертикали/горизонтали/диагоналям, лисы помечаются."""
    client = app_module.app.test_client()
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
    client = app_module.app.test_client()
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
    client = app_module.app.test_client()
    client.post("/start", data={"size": "8", "foxes": "5"})
    client.post("/move", data={"row": "99", "col": "99"})
    body = client.get("/").get_data(as_text=True)
    assert "Ходы: 0 / 100" in body


def test_size_clamp():
    """Некорректный размер поля приводится к 9."""
    client = app_module.app.test_client()
    client.post("/start", data={"size": "999", "foxes": "5"})
    body = client.get("/").get_data(as_text=True)
    assert body.count('class="cell hidden') == 81


def test_size_15():
    """Поле 15x15 доступно и использует мелкие клетки."""
    client = app_module.app.test_client()
    resp = client.post("/start", data={"size": "15", "foxes": "10"})
    assert resp.status_code == 302
    body = client.get("/").get_data(as_text=True)
    assert body.count('class="cell hidden') == 225
    assert 'cell hidden  small' in body


def test_exit_game():
    """Выход возвращает в стартовое меню."""
    client = app_module.app.test_client()
    client.post("/start", data={"size": "8", "foxes": "5"})
    client.post("/exit")
    body = client.get("/").get_data(as_text=True)
    assert "Новая игра" in body


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
