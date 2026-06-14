import csv
import json
import os
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

import run_apify

MIN_GOOD_ROWS = 20
WARSAW_TZ = ZoneInfo("Europe/Warsaw")
STATUS_FILE = run_apify.DATA_DIR / "last_run_status.json"


def now_warsaw_iso():
    return datetime.now(WARSAW_TZ).isoformat(timespec="seconds")


def write_status(status, message, **extra):
    run_apify.DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "attempted_at_warsaw": now_warsaw_iso(),
        "event_name": os.environ.get("GITHUB_EVENT_NAME", ""),
        "mode": "listing_only",
        "status": status,
        "message": message,
        **extra,
    }
    STATUS_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


def latest_rows_count():
    try:
        rows = json.loads(run_apify.LATEST_FILE.read_text(encoding="utf-8"))
    except Exception:
        return 0
    return len(rows) if isinstance(rows, list) else 0


def should_skip_scheduled_night_run():
    if os.environ.get("GITHUB_EVENT_NAME") != "schedule":
        return False
    now = datetime.now(WARSAW_TZ)
    print(f"Aktualny czas Warszawa: {now:%Y-%m-%d %H:%M:%S %Z}")
    if 0 <= now.hour < 6:
        write_status("skipped", "Pominięto automatyczne odświeżenie w godzinach 00:00-05:59 czasu polskiego.")
        return True
    return False


def to_int(value):
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except Exception:
        return None


def to_float(value):
    if value in (None, ""):
        return None
    try:
        return float(value)
    except Exception:
        return None


def to_bool(value):
    if value in (None, ""):
        return None
    value_text = str(value).lower()
    if value_text == "true":
        return True
    if value_text == "false":
        return False
    return None


def normalize_history_row(row):
    out = dict(row)
    for field in ("offer_id", "product_id", "product_parent_id"):
        out[field] = to_int(out.get(field))
    for field in ("price_gross", "price_with_code", "omnibus_price_web", "omnibus_price_app"):
        out[field] = to_float(out.get(field))
    out["available_in_store"] = to_bool(out.get("available_in_store"))
    for key, value in list(out.items()):
        if value == "":
            out[key] = None
    checked_at = out.get("checked_at")
    if not out.get("first_seen_at"):
        out["first_seen_at"] = checked_at
    out["price_changed_at"] = out.get("price_changed_at") or None
    out["previous_price_gross"] = out.get("previous_price_gross") or None
    out["previous_price_with_code"] = out.get("previous_price_with_code") or None
    out["price_changed_now"] = False
    out["is_new_product"] = False
    return out


def restore_latest_from_history():
    if not run_apify.HISTORY_FILE.exists() or run_apify.HISTORY_FILE.stat().st_size == 0:
        return 0
    groups = {}
    with run_apify.HISTORY_FILE.open("r", newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        for row in reader:
            checked_at = row.get("checked_at")
            if checked_at:
                groups.setdefault(checked_at, []).append(row)
    if not groups:
        return 0
    best_checked_at, best_rows = max(groups.items(), key=lambda item: len(item[1]))
    if len(best_rows) < MIN_GOOD_ROWS:
        return 0
    normalized = [normalize_history_row(row) for row in best_rows]
    run_apify.DATA_DIR.mkdir(parents=True, exist_ok=True)
    run_apify.LATEST_FILE.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Odtworzono latest_prices.json z historii: {len(normalized)} produktów z paczki {best_checked_at}.")
    return len(normalized)


def main():
    if should_skip_scheduled_night_run():
        return

    before = latest_rows_count()
    write_status("running", "Start pobierania aktualnej listy produktów ze strony promocji.", latest_rows_before=before)

    try:
        run_apify.main()
        write_status("success", "Pobrano aktualną listę produktów ze strony promocji Media Expert.", latest_rows_after=latest_rows_count())
        return
    except Exception as exc:
        print("Nie pobrano aktualnej listy produktów ze strony promocji.")
        print(f"Powód: {exc}")
        traceback.print_exc()
        current_count = latest_rows_count()
        if current_count >= MIN_GOOD_ROWS:
            write_status(
                "failed_kept_previous_listing_only",
                "Nie pobrano aktualnej listy ze strony promocji. Zostawiono poprzednią poprawną listę.",
                error=str(exc)[:2000],
                latest_rows_after=current_count,
            )
            return
        restored = restore_latest_from_history()
        if restored >= MIN_GOOD_ROWS:
            write_status(
                "failed_restored_from_history_listing_only",
                "Nie pobrano aktualnej listy ze strony promocji, odtworzono dane z historii.",
                error=str(exc)[:2000],
                latest_rows_after=restored,
            )
            return
        write_status("failed", "Nie pobrano listy ze strony promocji i nie udało się odtworzyć historii.", error=str(exc)[:2000])
        raise


if __name__ == "__main__":
    main()
