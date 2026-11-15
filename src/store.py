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
    "airlines",
    "stops",
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


def append_history(offers: Iterable[dict[str, Any]], observed_at: str) -> int:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    is_new = not HISTORY_PATH.exists()
    rows = 0
    with HISTORY_PATH.open("a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=HISTORY_FIELDS)
        if is_new:
            writer.writeheader()
        for offer in offers:
            row = {key: offer.get(key, "") for key in HISTORY_FIELDS}
            row["observed_at"] = observed_at
            row["airlines"] = "|".join(offer.get("airlines") or [])
            writer.writerow(row)
            rows += 1
    return rows


def read_history() -> list[dict[str, str]]:
    if not HISTORY_PATH.exists():
        return []
    with HISTORY_PATH.open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))
