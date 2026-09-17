import sqlite3
from datetime import datetime
from typing import Optional


def init_db(db_path: str) -> None:
    """
    Создаёт базу данных и таблицу расходов, если их ещё нет.
    """
    with sqlite3.connect(db_path) as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                amount REAL NOT NULL,
                category TEXT NOT NULL,
                comment TEXT,
                expense_date TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )

        cursor.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_expenses_user_date
            ON expenses(user_id, expense_date)
            """
        )

        connection.commit()


def add_expense(
    db_path: str,
    user_id: int,
    amount: float,
    category: str,
    comment: Optional[str] = None,
) -> None:
    """
    Добавляет новый расход в базу данных.
    """
    now = datetime.now()

    with sqlite3.connect(db_path) as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO expenses (
                user_id,
                amount,
                category,
                comment,
                expense_date,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                amount,
                category.lower(),
                comment,
                now.date().isoformat(),
                now.isoformat(timespec="seconds"),
            ),
        )

        connection.commit()


def get_summary(
    db_path: str,
    user_id: int,
    start_date: str,
    end_date: str,
) -> dict:
    """
    Возвращает статистику расходов пользователя за период.
    """
    with sqlite3.connect(db_path) as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT 
                COALESCE(SUM(amount), 0),
                COUNT(*)
            FROM expenses
            WHERE user_id = ?
              AND expense_date BETWEEN ? AND ?
            """,
            (user_id, start_date, end_date),
        )

        total, count = cursor.fetchone()

        cursor.execute(
            """
            SELECT 
                category,
                SUM(amount)
            FROM expenses
            WHERE user_id = ?
              AND expense_date BETWEEN ? AND ?
            GROUP BY category
            ORDER BY SUM(amount) DESC
            """,
            (user_id, start_date, end_date),
        )

        categories = cursor.fetchall()

    return {
        "total": total,
        "count": count,
        "categories": categories,
    }


def get_last_expenses(
    db_path: str,
    user_id: int,
    limit: int = 10,
) -> list[tuple]:
    """
    Возвращает последние расходы пользователя.
    """
    with sqlite3.connect(db_path) as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT 
                id,
                amount,
                category,
                comment,
                created_at
            FROM expenses
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        )

        return cursor.fetchall()


def delete_last_expense(
    db_path: str,
    user_id: int,
) -> Optional[tuple]:
    """
    Удаляет последний расход пользователя.
    """
    with sqlite3.connect(db_path) as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT 
                id,
                amount,
                category,
                comment
            FROM expenses
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (user_id,),
        )

        expense = cursor.fetchone()

        if expense is None:
            return None

        expense_id = expense[0]

        cursor.execute(
            """
            DELETE FROM expenses
            WHERE id = ?
            """,
            (expense_id,),
        )

        connection.commit()

        return expense


def delete_expense_by_id(
    db_path: str,
    user_id: int,
    expense_id: int,
) -> Optional[tuple]:
    """
    Удаляет расход по ID, но только если он принадлежит пользователю.
    """
    with sqlite3.connect(db_path) as connection:
        cursor = connection.cursor()

        cursor.execute(
            """
            SELECT 
                id,
                amount,
                category,
                comment
            FROM expenses
            WHERE id = ?
              AND user_id = ?
            """,
            (expense_id, user_id),
        )

        expense = cursor.fetchone()

        if expense is None:
            return None

        cursor.execute(
            """
            DELETE FROM expenses
            WHERE id = ?
              AND user_id = ?
            """,
            (expense_id, user_id),
        )

        connection.commit()

        return expense