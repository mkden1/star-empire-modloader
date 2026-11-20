"""trade_bot_slots_capture.py — persist TRADE_BOT_SLOTS_DATA for offline analysis.

The server computes slots_used as the SUM OF EACH ACTIVE BOT'S SHIP-TECH COST, not
one slot per bot (see protocol._on_trade_bot_slots_data) -- so this is the only
correct source for "how much of the 59-per-level slot pool is actually in use,"
never a count of bot names. One row per account (upserted by username -- the
newest snapshot replaces the last, this isn't a history), since each running
client only ever sees its own logged-in account's totals.
"""

from __future__ import annotations

import csv
import logging
import os
import time

import capture_settings
import scan_paths
from scan_writer import dispatch
from scan_data import (  # core: the read side of the scan files, shared with the scan archive and the galaxy map
    load_trade_bot_slots,
    trade_bot_slots_path as _default_out_path,
)  # noqa: F401

logger = logging.getLogger(__name__)

FIELDNAMES = ("username", "slots_used", "slots_total", "updated_at")


def _read_existing(path: str) -> dict[str, dict]:
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        return {r["username"]: r for r in csv.DictReader(f) if r.get("username")}


def record_trade_bot_slots(username: str, slots_used: int, slots_total: int,
                            out_path: str | None = None) -> None:
    """Upsert this account's current slot usage. Best-effort -- logs and swallows. In production
    the row is built here and the file read-modify-write is queued for scan_writer's thread."""
    try:
        if out_path is None and not capture_settings.enabled("trade_bot_slots"):
            return
        uname = str(username or "").strip()
        if not uname:
            return
        row = {
            "username": uname,
            "slots_used": str(int(slots_used)),
            "slots_total": str(int(slots_total)),
            "updated_at": str(int(time.time())),
        }
        dispatch(out_path, _upsert_slots_row, out_path, row)
    except Exception:
        logger.exception("trade_bot_slots_capture: failed to record trade bot slots")


def _upsert_slots_row(out_path: str | None, row: dict) -> None:
    try:
        path = out_path or _default_out_path()
        scan_paths.ensure_dir(path)
        rows = _read_existing(path)
        rows[row["username"]] = row
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            for key in sorted(rows):
                writer.writerow(rows[key])
    except Exception:
        logger.exception("trade_bot_slots_capture: failed to record trade bot slots")


