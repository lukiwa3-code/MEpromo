import csv
import json
import os
import traceback
from datetime import datetime
from zoneinfo import ZoneInfo

import run_apify

MIN_GOOD_ROWS = 20
WARSAW_TZ = ZoneInfo("Europe/Warsaw")


def latest_rows_count():
    try:
        rows = json.loads(run_apify.LATEST_FILE.read_text(encoding="utf-8"))
    except Exception:
        return 0
    return len(rows) if isinstance(rows, list) else 0


def scheduled_run_should_be_skipped():
    """Skip only automatic scheduled runs between 00:00 and 05:59 Warsaw time.

    Manual runs from the GitHub Actions button should always work, even at night.
    """
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name != "schedule":
        return False

    now_warsaw = datetime.now(WARSAW_TZ)
    print(f"Aktualny czas Warszawa: {now_warsaw:%Y-%m-%d %H:%M:%S %Z}")

    if 0 <= now_warsaw.hour < 6:
        print("Zaplanowane odswiezenie pominiete: przedzial 00:00-05:59 czasu polskiego.")
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
        print("Brak price_history.csv, nie mam z czego odtworzyc latest_prices.json.")
        return 0
    groups = {}
    try:
        with run_apify.HISTORY_FILE.open("r", newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            for row in reader:
                checked_at = row.get("checked_at")
                if checked_at:
                    groups.setdefault(checked_at, []).append(row)
    except Exception as exc:
        print(f"Nie udalo sie odczytac price_history.csv: {exc}")
        return 0
    if not groups:
        return 0
    best_checked_at, best_rows = max(groups.items(), key=lambda item: len(item[1]))
    if len(best_rows) < MIN_GOOD_ROWS:
        print(f"Najwieksza paczka w historii ma tylko {len(best_rows)} produktow.")
        return 0
    normalized = [normalize_history_row(row) for row in best_rows]
    run_apify.DATA_DIR.mkdir(parents=True, exist_ok=True)
    run_apify.LATEST_FILE.write_text(json.dumps(normalized, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Odtworzylem latest_prices.json z historii: {len(normalized)} produktow z paczki {best_checked_at}.")
    return len(normalized)


def main():
    if scheduled_run_should_be_skipped():
        return

    try:
        run_apify.main()
    except Exception as exc:
        existing_count = latest_rows_count()
        print("\nUWAGA: Biezace odswiezenie nie pobralo nowych danych.")
        print(f"Powod: {exc}")
        print("Pelny traceback dla diagnostyki:")
        traceback.print_exc()
        if existing_count >= MIN_GOOD_ROWS:
            print(f"\nZostawiam poprzednie poprawne dane: {existing_count} produktow.")
            return
        print(f"\nlatest_prices.json ma tylko {existing_count} produktow. Odtwarzam z historii.")
        restored_count = restore_latest_from_history()
        if restored_count >= MIN_GOOD_ROWS:
            print("Workflow koncze jako zielony po odtworzeniu danych z historii.")
            return
        raise


if __name__ == "__main__":
    main()
