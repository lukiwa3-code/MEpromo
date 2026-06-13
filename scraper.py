import csv
import json
import os
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


BASE_URL = "https://www.mediaexpert.pl"
LISTING_URL = "https://www.mediaexpert.pl/zabawki/lego/lego/promocje_cena-z-kodem?limit=50"

# GitHub Actions dostaje 403 na stronie listingu, więc startujemy od znanego JSON-a spark-state.
# Możesz podmienić przez zmienną środowiskową SPARK_STATE_URL albo podać kilka URL-i w SPARK_STATE_URLS.
DEFAULT_SPARK_STATE_URL = "https://www.mediaexpert.pl/spark-state/30272cb24e-96a041-f2b27e-7efc1b"

DATA_DIR = Path("data")
LATEST_FILE = DATA_DIR / "latest_prices.json"
HISTORY_FILE = DATA_DIR / "price_history.csv"

HEADERS = {
    "accept": "*/*",
    "accept-language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    "referer": LISTING_URL,
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
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


def request_json(url):
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.json()


def request_text(url):
    response = requests.get(url, headers=HEADERS, timeout=30)
    response.raise_for_status()
    return response.text


def find_spark_state_url_from_listing(page_url):
    """
    Awaryjnie: próbuje znaleźć aktualny /spark-state/... na stronie listingu.
    Na GitHub Actions Media Expert może zwracać 403, dlatego to NIE jest główna metoda.
    """
    html = request_text(page_url)

    match = re.search(r"/spark-state/[a-zA-Z0-9\-]+", html)

    if not match:
        raise RuntimeError("Nie znaleziono linku /spark-state/... na stronie listingu.")

    return BASE_URL + match.group(0)


def get_configured_spark_urls():
    """
    Priorytet:
    1. SPARK_STATE_URLS - kilka adresów po przecinku, np. page1,page2
    2. SPARK_STATE_URL - jeden adres
    3. DEFAULT_SPARK_STATE_URL - adres z Twojego cURL-a
    """
    urls_many = os.environ.get("SPARK_STATE_URLS", "").strip()
    if urls_many:
        return [url.strip() for url in urls_many.split(",") if url.strip()]

    url_one = os.environ.get("SPARK_STATE_URL", "").strip()
    if url_one:
        return [url_one]

    return [DEFAULT_SPARK_STATE_URL]


def fetch_spark_state_direct(spark_url):
    return request_json(spark_url), spark_url


def fetch_spark_state_with_fallback(page_url):
    """
    Najpierw próbuje znany /spark-state/...,
    a dopiero gdy to nie działa, próbuje znaleźć nowy link na stronie listingu.
    """
    configured_urls = get_configured_spark_urls()

    last_error = None

    for spark_url in configured_urls:
        try:
            return fetch_spark_state_direct(spark_url)
        except Exception as exc:
            last_error = exc
            print(f"Nie udało się pobrać skonfigurowanego spark-state: {spark_url}")
            print(f"Błąd: {exc}")

    print("Próbuję znaleźć spark-state przez stronę listingu...")
    try:
        found_url = find_spark_state_url_from_listing(page_url)
        return fetch_spark_state_direct(found_url)
    except Exception as exc:
        raise RuntimeError(
            "Nie udało się pobrać spark-state ani przez bezpośredni URL, "
            "ani przez stronę listingu. "
            f"Ostatni błąd direct: {last_error}. Błąd listing: {exc}"
        ) from exc


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
                if not isinstance(product_data, dict):
                    continue

                product_id = product_data.get("product_id")

                if product_id:
                    result[str(product_id)] = str(lego_code)

    return result


def get_product_state(state):
    return state.get("Service:GenericProductListService.state", {})


def extract_promo_web(offer):
    """
    Media Expert raz używa snake_case (_for_action_price, code_price),
    a w GraphQL czasem camelCase (ForActionPrice, codePrice).
    Ten scraper obsługuje oba warianty.
    """
    sales_channel = offer.get("promotionPricesSalesChannel") or {}

    web = sales_channel.get("web") or {}

    promo = (
        web.get("_for_action_price")
        or web.get("ForActionPrice")
        or web.get("forActionPrice")
        or {}
    )

    if not isinstance(promo, dict):
        return {}

    return promo


def get_promo_code_price_amount(promo):
    code_price = promo.get("code_price") or promo.get("codePrice") or {}

    if isinstance(code_price, dict):
        return code_price.get("amount")

    return None


def get_promo_value(promo, snake_name, camel_name):
    return promo.get(snake_name) or promo.get(camel_name)


def extract_offer(offer, checked_at, code_map):
    product_id = offer.get("product_id") or offer.get("productId")
    product_id_str = str(product_id) if product_id is not None else ""

    promo_web = extract_promo_web(offer)
    code_price = get_promo_code_price_amount(promo_web)

    name = offer.get("name")
    lego_code = code_map.get(product_id_str) or extract_lego_code_from_name(name)

    link = offer.get("link") or ""
    full_url = BASE_URL + link if link.startswith("/") else link

    availability = offer.get("availability") or {}

    return {
        "checked_at": checked_at,
        "shop": "Media Expert",
        "lego_code": lego_code,
        "offer_id": offer.get("id"),
        "product_id": product_id,
        "product_parent_id": offer.get("product_parent_id") or offer.get("productParentId"),
        "name": name,
        "url": full_url,
        "price_gross": grosze_to_pln(offer.get("price_gross") or offer.get("priceGross")),
        "price_with_code": grosze_to_pln(code_price),
        "promo_code": get_promo_value(promo_web, "code", "code"),
        "promo_date_from": get_promo_value(promo_web, "date_from", "dateFrom"),
        "promo_date_to": get_promo_value(promo_web, "date_to", "dateTo"),
        "omnibus_price_web": grosze_to_pln(
            offer.get("omnibus_price_web") or offer.get("omnibusPriceWeb")
        ),
        "omnibus_price_app": grosze_to_pln(
            offer.get("omnibus_price_app") or offer.get("omnibusPriceApp")
        ),
        "availability": availability.get("display_name") or availability.get("displayName"),
        "availability_type": availability.get("type"),
        "available_in_store": offer.get("available_in_store") or offer.get("availableInStore"),
    }


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


def extract_rows_from_state(state, checked_at, seen_product_ids):
    product_state = get_product_state(state)
    offers = product_state.get("loadedOffers", [])
    code_map = extract_code_map(state)

    rows = []

    for offer in offers:
        product_id = offer.get("product_id") or offer.get("productId")

        if not product_id or product_id in seen_product_ids:
            continue

        seen_product_ids.add(product_id)
        rows.append(extract_offer(offer, checked_at, code_map))

    return rows


def collect_all_products():
    checked_at = datetime.now(ZoneInfo("Europe/Warsaw")).isoformat(timespec="seconds")

    configured_spark_urls = get_configured_spark_urls()
    seen_product_ids = set()
    all_rows = []

    # Jeżeli podasz kilka spark-state przez SPARK_STATE_URLS, pobierze wszystkie.
    if len(configured_spark_urls) > 1:
        print(f"Używam {len(configured_spark_urls)} skonfigurowanych URL-i spark-state.")

        for spark_url in configured_spark_urls:
            state, used_url = fetch_spark_state_direct(spark_url)
            rows = extract_rows_from_state(state, checked_at, seen_product_ids)
            all_rows.extend(rows)
            print(f"Spark-state: {used_url} | produktów: {len(rows)}")

        return all_rows

    # Domyślnie pobieramy znany spark-state z Twojego cURL-a.
    first_state, first_spark_url = fetch_spark_state_with_fallback(get_page_url(1))

    product_state = get_product_state(first_state)
    offers_count = product_state.get("offersCount")
    total_pages = get_total_pages(first_state)

    rows = extract_rows_from_state(first_state, checked_at, seen_product_ids)
    all_rows.extend(rows)

    print(f"Spark-state: {first_spark_url}")
    print(f"Zapisano z pierwszego spark-state: {len(rows)} produktów")

    if offers_count:
        print(f"offersCount według strony: {offers_count}")

    if total_pages > 1:
        print(
            "UWAGA: strona pokazuje więcej niż 1 stronę wyników, "
            "ale GitHub może blokować listing kodem 403. "
            "Jeżeli chcesz pobrać kolejne strony, dodaj ich adresy spark-state "
            "do zmiennej SPARK_STATE_URLS po przecinku."
        )

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
