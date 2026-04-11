def timespan_to_minutes(timespan: str) -> int:
    """
    Конвертирует строку времени в минуты.

    Поддерживает форматы:\n
    - "02:05:30" -> 2 дня, 5 часов, 30 минут = 3030 минут
    - "05:30" -> 5 часов, 30 минут = 330 минут
    - "120" -> 120 минут
    - None -> 0

    Args:
        timespan: Строка времени в формате dd:hh:mm, hh:mm или mm

    Returns:
        Количество минут
    """

    if timespan is None:
        return 0

    if isinstance(timespan, int | float):
        return int(timespan)

    if not isinstance(timespan, str):
        return 0

    timespan = timespan.strip()
    if not timespan:
        return 0

    try:
        parts = list(map(int, timespan.split(":")))

        if len(parts) == 3:
            days, hours, minutes = parts
            return days * 1440 + hours * 60 + minutes
        elif len(parts) == 2:
            hours, minutes = parts
            return hours * 60 + minutes
        elif len(parts) == 1:
            return parts[0]  # Просто минуты
        else:
            return 0
    except (ValueError, AttributeError):
        return 0
