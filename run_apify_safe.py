import traceback

import run_apify


MIN_EXISTING_ROWS_FOR_SOFT_FAIL = 1


def latest_rows_count():
    try:
        rows = run_apify.json.loads(run_apify.LATEST_FILE.read_text(encoding="utf-8"))
    except Exception:
        return 0
    return len(rows) if isinstance(rows, list) else 0


def main():
    try:
        run_apify.main()
    except Exception as exc:
        existing_count = latest_rows_count()

        print("\nUWAGA: Bieżące odświeżenie nie pobrało nowych danych.")
        print(f"Powód: {exc}")
        print("Pełny traceback dla diagnostyki:")
        traceback.print_exc()

        if existing_count >= MIN_EXISTING_ROWS_FOR_SOFT_FAIL:
            print(
                "\nZostawiam poprzednie poprawne dane bez zmian, "
                f"bo latest_prices.json nadal ma {existing_count} produktów."
            )
            print("Workflow kończę jako zielony, żeby chwilowy timeout Apify nie psuł strony.")
            return

        print("\nNie ma poprzednich danych do zachowania, więc workflow musi zakończyć się błędem.")
        raise


if __name__ == "__main__":
    main()
