import re
from typing import Optional


class ExpenseParseError(Exception):
    """
    Ошибка разбора пользовательского расхода.
    """
    pass


UNITS = {
    "ноль": 0,
    "один": 1,
    "одна": 1,
    "одно": 1,
    "два": 2,
    "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
}

TEENS = {
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
    "тринадцать": 13,
    "четырнадцать": 14,
    "пятнадцать": 15,
    "шестнадцать": 16,
    "семнадцать": 17,
    "восемнадцать": 18,
    "девятнадцать": 19,
}

TENS = {
    "двадцать": 20,
    "тридцать": 30,
    "сорок": 40,
    "пятьдесят": 50,
    "шестьдесят": 60,
    "семьдесят": 70,
    "восемьдесят": 80,
    "девяносто": 90,
}

HUNDREDS = {
    "сто": 100,
    "двести": 200,
    "триста": 300,
    "четыреста": 400,
    "пятьсот": 500,
    "шестьсот": 600,
    "семьсот": 700,
    "восемьсот": 800,
    "девятьсот": 900,
}

THOUSANDS = {
    "тысяча",
    "тысячи",
    "тысяч",
    "тысячу",
}

CURRENCY_WORDS = {
    "рубль",
    "рубля",
    "рублей",
    "руб",
    "р",
}


def tokenize_text(text: str) -> list[str]:
    """
    Делит текст на токены.

    Примеры:
    "250 еда кофе" -> ["250", "еда", "кофе"]
    "250,5 продукты" -> ["250.5", "продукты"]
    "250р еда" -> ["250", "р", "еда"]
    """
    prepared_text = text.lower().replace(",", ".")

    return re.findall(
        r"\d+(?:\.\d+)?|[а-яёa-z]+",
        prepared_text,
        flags=re.IGNORECASE,
    )


def parse_numeric_amount(token: str) -> Optional[float]:
    """
    Пытается разобрать сумму, если она написана цифрами.
    """
    try:
        amount = float(token)
    except ValueError:
        return None

    if amount <= 0:
        raise ExpenseParseError("Сумма должна быть больше нуля.")

    return amount


def parse_russian_number_prefix(tokens: list[str]) -> tuple[Optional[float], int]:
    """
    Пытается разобрать сумму, если она написана словами.

    Примеры:
    ["двести", "пятьдесят", "еда"] -> 250, consumed=2
    ["одна", "тысяча", "двести", "продукты"] -> 1200, consumed=3
    """
    total = 0
    current = 0
    consumed = 0
    has_number = False

    for index, token in enumerate(tokens):
        if token in HUNDREDS:
            current += HUNDREDS[token]
            has_number = True

        elif token in TENS:
            current += TENS[token]
            has_number = True

        elif token in TEENS:
            current += TEENS[token]
            has_number = True

        elif token in UNITS:
            current += UNITS[token]
            has_number = True

        elif token in THOUSANDS:
            if current == 0:
                current = 1

            total += current * 1000
            current = 0
            has_number = True

        else:
            break

        consumed = index + 1

    if not has_number:
        return None, 0

    amount = total + current

    if amount <= 0:
        raise ExpenseParseError("Сумма должна быть больше нуля.")

    return float(amount), consumed


def skip_currency_words(tokens: list[str], start_index: int) -> int:
    """
    Пропускает слова вроде 'рублей', если пользователь их произнёс.
    """
    index = start_index

    while index < len(tokens) and tokens[index] in CURRENCY_WORDS:
        index += 1

    return index


def parse_expense(text: str) -> tuple[float, str, Optional[str]]:
    """
    Разбирает расход из текста.

    Поддерживаемые форматы:
    250 еда кофе
    250 рублей еда кофе
    двести пятьдесят еда кофе
    одна тысяча двести транспорт такси
    """
    tokens = tokenize_text(text)

    if len(tokens) < 2:
        raise ExpenseParseError(
            "Нужно указать сумму и категорию. Например: 250 еда кофе"
        )

    amount = parse_numeric_amount(tokens[0])
    consumed = 1

    if amount is None:
        amount, consumed = parse_russian_number_prefix(tokens)

    if amount is None:
        raise ExpenseParseError(
            "Не удалось понять сумму. Напиши, например: 250 еда кофе"
        )

    category_index = skip_currency_words(tokens, consumed)

    if category_index >= len(tokens):
        raise ExpenseParseError(
            "Нужно указать категорию. Например: 250 еда кофе"
        )

    category = tokens[category_index]

    comment_tokens = tokens[category_index + 1:]
    comment = " ".join(comment_tokens) if comment_tokens else None

    return amount, category, comment