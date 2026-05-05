#!/usr/bin/env python3
"""
Extract and normalize Yandex settlement names (cities and settlements only).

Loads Yandex JSON, keeps only items marked as city, normalizes names
and writes a JSON object keyed by
normalized settlement name.
"""

import argparse
import json
import logging
import re
import sys
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

CITY_PREFIX_RE = r"^(г\.|г\b|город\b)\s*"

SERVICE_WORDS = [
    "село",
    "деревня",
    "поселок",
    "им",
    "имени",
    "пгт",
    "ст-ца",
    "рп",
    "с\\.",
    "д\\.",
    "п",
]

STATION_WORDS = [
    "автостанция",
    "авт\\.ст\\.",
    "авт\\.ост\\.",
    "вокзал",
    "вокз\\.",
    "вокз",
    "станция",
    "станции",
    "остров",
    "ост",
]

SERVICE_WORDS_RE = r"\b(" + "|".join(SERVICE_WORDS) + r")\b"
STATION_WORDS_RE = r"\b(" + "|".join(STATION_WORDS) + r")\b"


def clean_name(name: Any) -> str:
    """Normalize raw name: lowercase, trim, replace yo with ye, collapse spaces."""
    if name is None:
        return ""
    value = str(name).strip().lower()
    value = value.replace("ё", "е")
    value = re.sub(r"\s+", " ", value)
    return value


def clean_settlement_name(name: Any) -> str:
    """Normalize settlement name using the notebook logic."""
    if not name:
        return ""

    value = clean_name(name)

    value = re.sub(CITY_PREFIX_RE, "", value)
    value = re.sub(SERVICE_WORDS_RE, "", value)
    value = re.sub(STATION_WORDS_RE, "", value)

    value = re.sub(r"\(.+\)", "", value)
    value = value.replace("-", " ")

    value = re.sub(r"[^a-z0-9а-яё\s]", "", value)
    value = re.sub(r"\s+", " ", value).strip()

    return value


def is_city_item(item: dict[str, Any]) -> bool:
    transport_type = str(item.get("transport_type") or "").strip().lower()
    station_type = str(item.get("station_type") or "").strip().lower()
    return transport_type == "city" or station_type == "city"


def extract_settlements(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}

    for item in items:
        if not is_city_item(item):
            continue

        name = str(item.get("name") or "").strip()
        if not name:
            continue

        normalized = clean_settlement_name(name)
        if not normalized:
            continue

        if normalized in results:
            continue

        results[normalized] = {
            "name": name,
            "yandex_code": item.get("yandex_code"),
        }

    return results


def save_to_json(
    records: dict[str, dict[str, Any]], filename: str | None = None
) -> str:
    if filename is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"yandex_settlements_normalized_{timestamp}.json"

    with open(filename, "w", encoding="utf-8") as file:
        json.dump(records, file, ensure_ascii=False, indent=2)

    logger.info("Saved %s normalized settlements to %s", len(records), filename)
    return filename


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Normalize settlement names from Yandex JSON data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python app/scripts/normalize_yandex_settlements.py \
    --input-file yandex_rasp_locations.json

  python app/scripts/normalize_yandex_settlements.py \
    --input-file yandex_rasp_locations.json --output-file yandex_settlements.json
        """,
    )

    parser.add_argument(
        "--input-file",
        type=str,
        required=True,
        help="Yandex JSON file with locations",
    )
    parser.add_argument(
        "--output-file",
        type=str,
        help="Output JSON file name "
        "(default: yandex_settlements_normalized_TIMESTAMP.json)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    logging.basicConfig(
        level=logging.INFO if not args.verbose else logging.DEBUG,
        format="%(asctime)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    logger.info("Loading Yandex locations from %s", args.input_file)
    with open(args.input_file, encoding="utf-8") as file:
        items = json.load(file)

    if not isinstance(items, list):
        logger.error("Expected a list in the input JSON file.")
        sys.exit(1)

    records = extract_settlements(items)
    if not records:
        logger.warning("No settlements found in input file.")

    output_file = save_to_json(records, args.output_file)
    logger.info("Normalized settlements saved to %s", output_file)


if __name__ == "__main__":
    main()
