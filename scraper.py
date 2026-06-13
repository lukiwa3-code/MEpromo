import csv
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


BASE_URL = "https://www.mediaexpert.pl"
LISTING_URL = "https://www.mediaexpert.pl/zabawki/lego/lego/promocje_cena-z-kodem?limit=50"

DATA_DIR = Path("data")
LATEST_FILE = DATA_DIR / "latest_prices.json"
HISTORY_FILE = DATA_DIR / "price_history.csv"

HEADERS = {
    "accept": "*/*",
    "user-agent": "Mozilla/5.0",
    "referer": LISTING_URL,
}


def grosze_to_pln(value):
    """Media Expert podaje ceny jako grosze, np. 62900 = 629.00 PLN."""
    if value is None:
        return None

    try:
        return round(int(value) / 100, 2)
    except (TypeError, ValueError):
        return None


def get_nested(data, *keys):
    current = data

    for key in keys:
        if not isinstance(current, dict):
            return None

        current = current.get(key)

    return current


def find_spark_state_url(page_url):
    """
    Pobiera stronę listingu i znajduje aktualny link /spark-state/...
    Nie pobieramy cen z HTML. HTML służy tylko jako bramka do JSON-a.
    """
    response = requests.get(page_url, headers=HEADERS, timeout=30)
    response.raise_for_status()

    html = response.text
    match = re.search(r"/spark-state/[a-zA-Z0-9\-]+", html)

    if not match:
        raise RuntimeError("Nie znaleziono linku /spark-state/... na stronie listingu.")

    return BASE_URL + match.group(0)


def fetch_spark_state(page_url):
    spark_url = find_spark_state_url(page_url)

    response = requests.get(spark_url, headers=HEADERS, timeout=30)
    response.raise_for_status()

    return response.json(), spark_url


def extract_lego_code_from_name(name):
    if not name:
        return None

    match = re.search(r"\b(\d{5})\b", name)

    if match:
        return match.group(1)

    return None


def extract_code_map(state):
    """
    Tworzy mapę:
    product_id -> kod LEGO, np. 3650231 -> 10300

    Dane mogą siedzieć w:
    ProductList:ProductListAdditionalService.state.offerAdditionals[].variants[].offers
    """
    result = {}

    additionals = get_nested(
        state,
        "ProductList:ProductListAdditionalService.state",
        "offerAdditionals",
    ) or []

    for item in additionals:
        variants = item.get("variants") or []

        for variant in variants:
            offers = variant.get("offers") or {}

            for lego_code, product_data in offers.items():
                product_id = product_data.get("product_id")

                if product_id:
                    result[str(product_id)] = str(lego_code)

    return result


def extract_offer(offer, checked_at, code_map):
    product_id = offer.get("product_id")
    product_id_str = str(product_id) if product_id is not None else ""

    promo_web = get_nested(
        offer,
        "promotionPricesSalesChannel",
        "web",
        "_for_action_price",
    ) or {}

    code_price = get_nested(promo_web, "code_price", "amount")

    name = offer.get("name")
    lego_code = code_map.get(product_id_str) or extract_lego_code_from_name(name)

    link = offer.get("link") or ""
    full_url = BASE_URL + link if link.startswith("/") else link

    return {
        "checked_at": checked_at,
        "shop": "Media Expert",
        "lego_code": lego_code,
        "offer_id": offer.get("id"),
        "product_id": product_id,
        "product_parent_id": offer.get("product_parent_id"),
        "name": name,
        "url": full_url,
        "price_gross": grosze_to_pln(offer.get("price_gross")),
        "price_with_code": grosze_to_pln(code_price),
        "promo_code": promo_web.get("code"),
        "promo_date_from": promo_web.get("date_from"),
        "promo_date_to": promo_web.get("date_to"),
        "omnibus_price_web": grosze_to_pln(offer.get("omnibus_price_web")),
        "omnibus_price_app": grosze_to_pln(offer.get("omnibus_price_app")),
        "availability": get_nested(offer, "availability", "display_name"),
        "availability_type": get_nested(offer, "availability", "type"),
        "available_in_store": offer.get("available_in_store"),
    }


def get_product_state(state):
    return state.get("Service:GenericProductListService.state", {})


def get_total_pages(state):
    product_state = get_product_state(state)

    total_pages = product_state.get("totalPages")

    if total_pages:
        return int(total_pages)

    offers_count = product_state.get("offersCount")
    items_per_page = product_state.get("itemsPerPage") or 50

    if offers_count:
        return max(1, (int(offers_count) + int(items_per_page) - 1) // int(items_per_page))

    return 1


def get_page_url(page_number):
    if page_number == 1:
        return LISTING_URL

    separator = "&" if "?" in LISTING_URL else "?"
    return f"{LISTING_URL}{separator}page={page_number}"


def collect_all_products():
    checked_at = datetime.now(ZoneInfo("Europe/Warsaw")).isoformat(timespec="seconds")

    first_state, first_spark_url = fetch_spark_state(get_page_url(1))
    total_pages = get_total_pages(first_state)

    all_rows = []
    seen_product_ids = set()

    print(f"Znaleziono stron: {total_pages}")
    print(f"Spark-state page 1: {first_spark_url}")

    for page in range(1, total_pages + 1):
        if page == 1:
            state = first_state
            spark_url = first_spark_url
        else:
            state, spark_url = fetch_spark_state(get_page_url(page))

        product_state = get_product_state(state)
        offers = product_state.get("loadedOffers", [])
        code_map = extract_code_map(state)

        print(f"Page {page}: {len(offers)} produktów, spark: {spark_url}")

        for offer in offers:
            product_id = offer.get("product_id")

            if not product_id or product_id in seen_product_ids:
                continue

            seen_product_ids.add(product_id)
            row = extract_offer(offer, checked_at, code_map)
            all_rows.append(row)

    return all_rows


def save_latest(rows):
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    LATEST_FILE.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_history(rows):
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not rows:
        return

    file_exists = HISTORY_FILE.exists()

    with HISTORY_FILE.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))

        if not file_exists:
            writer.writeheader()

        writer.writerows(rows)


def main():
    rows = collect_all_products()

    save_latest(rows)
    append_history(rows)

    print(f"Zapisano produktów: {len(rows)}")
    print(f"Plik aktualny: {LATEST_FILE}")
    print(f"Historia: {HISTORY_FILE}")


if __name__ == "__main__":
    main()
