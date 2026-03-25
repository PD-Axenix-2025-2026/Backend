from enum import StrEnum
from typing import TypeVar

from fastapi import HTTPException

EnumT = TypeVar("EnumT", bound=StrEnum)


def parse_csv_enum_values(
    raw_value: str | None,
    *,
    enum_type: type[EnumT],
    parameter_name: str,
) -> tuple[EnumT, ...]:
    if raw_value is None or raw_value.strip() == "":
        return ()

    values: list[EnumT] = []
    for item in raw_value.split(","):
        normalized_value = item.strip()
        if not normalized_value:
            continue
        try:
            values.append(enum_type(normalized_value))
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail=f"Unsupported value '{normalized_value}' for '{parameter_name}'",
            ) from exc

    return tuple(dict.fromkeys(values))
