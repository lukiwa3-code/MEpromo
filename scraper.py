import csv
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

BASE_URL = "https://www.mediaexpert.pl"
LISTING_URL = "https://www.mediaexpert.pl/zabawki/lego/lego/promocje_cena-z-kodem?limit=50"

DATA_DIR = Path("data")
LATEST_FILE = DATA_DIR / "latest_prices.json"
HISTORY_FILE = DATA_DIR / "price_history.csv"


def grosze_to_pln(value):
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


def find_first_key(obj, key_name):
    if isinstance(obj, dict):
        if key_name in obj:
            return obj[key_name]
        for value in obj.values():
            found = find_first_key(value, key_name)
            if found is not None:
                return found
    if isinstance(obj, list):
        for item in obj:
            found = find_first_key(item, key_name)
            if found is not None:
                return found
    return None


def get_page_url(page_number):
    if page_number == 1:
        return LISTING_URL
    return f"{LISTING_URL}&page={page_number}"


def get_loaded_offers(state):
    direct = get_nested(state, "Service:GenericProductListService.state", "loadedOffers")
    if isinstance(direct, list):
        return direct
    found = find_first_key(state, "loadedOffers")
    return found if isinstance(found, list) else []


def get_total_pages(state):
    product_state = state.get("Service:GenericProductListService.state", {})
    total_pages = product_state.get("totalPages")
    if total_pages:
        return int(total_pages)
    return 1


def extract_lego_code_from_name(name):
    if not name:
        return None
    match = re.search(r"\b(\d{5})\b", name)
    return match.group(1) if match else None


def extract_code_map(state):
    result = {}
    additionals = get_nested(state, "ProductList:ProductListAdditionalService.state", "offerAdditionals") or []
    for item in additionals:
        for variant in item.get("variants") or []:
            for lego_code, product_data in (variant.get("offers") or {}).items():
                if isinstance(product_data, dict) and product_data.get("product_id"):
                    result[str(product_data["product_id"])] = str(lego_code)
    return result


def extract_offer(offer, checked_at, code_map):
    product_id = offer.get("product_id") or offer.get("productId")
    promo = get_nested(offer, "promotionPricesSalesChannel", "web", "_for_action_price") or {}
    code_price = get_nested(promo, "code_price", "amount")
    availability = offer.get("availability") or {}
    name = offer.get("name")
    link = offer.get("link") or ""

    return {
        "checked_at": checked_at,
        "shop": "Media Expert",
        "lego_code": code_map.get(str(product_id)) or extract_lego_code_from_name(name),
        "offer_id": offer.get("id"),
        "product_id": product_id,
        "name": name,
        "url": BASE_URL + link if link.startswith("/") else link,
        "price_gross": grosze_to_pln(offer.get("price_gross") or offer.get("priceGross")),
        "price_with_code": grosze_to_pln(code_price),
        "promo_code": promo.get("code"),
        "promo_date_from": promo.get("date_from"),
        "promo_date_to": promo.get("date_to"),
        "omnibus_price_web": grosze_to_pln(offer.get("omnibus_price_web")),
        "availability": availability.get("display_name") or availability.get("displayName"),
        "availability_type": availability.get("type"),
    }


def capture_state(page, url):
    with page.expect_response(lambda r: "/spark-state/" in r.url and r.status == 200, timeout=90000) as response_info:
        page.goto(url, wait_until="domcontentloaded", timeout=90000)
    response = response_info.value
    print(f"Spark-state: {response.url}")
    return response.json()


def collect_all_products():
    checked_at = datetime.now(ZoneInfo("Europe/Warsaw")).isoformat(timespec="seconds")
    rows = []
    seen = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(locale="pl-PL", viewport={"width": 1365, "height": 900})
        page = context.new_page()

        first_state = capture_state(page, get_page_url(1))
        total_pages = get_total_pages(first_state)

        for page_number in range(1, total_pages + 1):
            state = first_state if page_number == 1 else capture_state(page, get_page_url(page_number))
            code_map = extract_code_map(state)
            offers = get_loaded_offers(state)
            print(f"Strona {page_number}: {len(offers)} produktów")

            for offer in offers:
                product_id = offer.get("product_id") or offer.get("productId")
                if not product_id or product_id in seen:
                    continue
                seen.add(product_id)
                rows.append(extract_offer(offer, checked_at, code_map))

        browser.close()

    return rows


def save_latest(rows):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_FILE.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def append_history(rows):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if not rows:
        return
    exists = HISTORY_FILE.exists()
    with HISTORY_FILE.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        if not exists:
            writer.writeheader()
        writer.writerows(rows)


def main():
    rows = collect_all_products()
    if not rows:
        raise RuntimeError("Nie pobrano produktów.")
    save_latest(rows)
    append_history(rows)
    print(f"Zapisano produktów: {len(rows)}")


if __name__ == "__main__":
    main()
