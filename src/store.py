"""資料存取層：最新快照寫成 JSON，歷史觀測累積成 CSV。"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SNAPSHOT_PATH = DATA_DIR / "deals.json"
HISTORY_PATH = DATA_DIR / "history.csv"

HISTORY_FIELDS = [
    "observed_at",
    "from_airport",
    "to_airport",
    "departure_date",
    "price",
    "currency",
    "airlines",
    "stops",
    "transfer_airports",
    "duration_minutes",
    "departure_time",
    "arrival_time",
    "is_best",
    "provider",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_snapshot(payload: dict[str, Any]) -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return SNAPSHOT_PATH


def read_snapshot() -> dict[str, Any] | None:
    if not SNAPSHOT_PATH.exists():
        return None
    return json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))


def append_history(
    offers: Iterable[dict[str, Any]], observed_at: str, currency: str = "EUR"
) -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    existing_rows: list[dict[str, str]] = []
    needs_header = not HISTORY_PATH.exists()
    if not needs_header:
        with HISTORY_PATH.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            old_fields = reader.fieldnames or []
            existing_rows = list(reader)
        if old_fields != HISTORY_FIELDS:
            # 保留舊紀錄，但幣別留空，避免混入新的 EUR 價格基準。
            with HISTORY_PATH.open("w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=HISTORY_FIELDS)
                writer.writeheader()
                writer.writerows(
                    {key: row.get(key, "") for key in HISTORY_FIELDS}
                    for row in existing_rows
                )
            needs_header = False

    rows = 0
    with HISTORY_PATH.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=HISTORY_FIELDS)
        if needs_header:
            writer.writeheader()
        for offer in offers:
            row = {key: offer.get(key, "") for key in HISTORY_FIELDS}
            row["observed_at"] = observed_at
            row["currency"] = currency
            row["airlines"] = "|".join(offer.get("airlines") or [])
            row["transfer_airports"] = "|".join(offer.get("transfer_airports") or [])
            writer.writerow(row)
            rows += 1
    return rows


def read_history() -> list[dict[str, str]]:
    if not HISTORY_PATH.exists():
        return []
    with HISTORY_PATH.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))
