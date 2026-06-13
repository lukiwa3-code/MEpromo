import csv
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


BASE_URL = "https://www.mediaexpert.pl"
LISTING_URL = "https://www.mediaexpert.pl/zabawki/lego/lego/promocje_cena-z-kodem?limit=50"

DATA_DIR = Path("data")
LATEST_FILE = DATA_DIR / "latest_prices.json"
HISTORY_FILE = DATA_DIR / "price_history.csv"
DEBUG_HTML_FILE = Path("debug_page.html")
DEBUG_TEXT_FILE = Path("debug_page.txt")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


# -------------------------
# Pomocnicze konwersje
# -------------------------


def grosze_to_pln(value):
    if value is None:
        return None
    try:
        return round(int(value) / 100, 2)
    except (TypeError, ValueError):
        return None


def price_text_to_float(text):
    """Obsługuje formaty: 743,71 zł, 1 299,00 zł, 629 00 zł."""
    if not text:
        return None

    text = text.replace("\xa0", " ").strip()

    match = re.search(r"((?:\d{1,3}(?:\s\d{3})*|\d+)[,.]\d{2})\s*zł", text)
    if match:
        raw = match.group(1).replace(" ", "").replace(",", ".")
        try:
            return round(float(raw), 2)
        except ValueError:
            return None

    # Media Expert potrafi rozbić cenę na format typu: 629 00 zł
    match = re.search(r"((?:\d{1,3}(?:\s\d{3})*|\d+))\s+(\d{2})\s*zł", text)
    if match:
        raw = f"{match.group(1).replace(' ', '')}.{match.group(2)}"
        try:
            return round(float(raw), 2)
        except ValueError:
            return None

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
    elif isinstance(obj, list):
        for item in obj:
            found = find_first_key(item, key_name)
            if found is not None:
                return found
    return None


# -------------------------
# URL-e i dane JSON
# -------------------------


def get_page_url(page_number):
    if page_number == 1:
        return LISTING_URL
    separator = "&" if "?" in LISTING_URL else "?"
    return f"{LISTING_URL}{separator}page={page_number}"


def get_loaded_offers(state):
    direct = get_nested(state, "Service:GenericProductListService.state", "loadedOffers")
    if isinstance(direct, list):
        return direct
    found = find_first_key(state, "loadedOffers")
    return found if isinstance(found, list) else []


def get_total_pages_from_state(state):
    product_state = state.get("Service:GenericProductListService.state", {})
    total_pages = product_state.get("totalPages")
    if total_pages:
        return int(total_pages)
    return 1


def get_total_pages_from_text(text):
    # Na stronie jest zwykle fragment: "z 2"
    matches = re.findall(r"(?m)^z\s+(\d+)\s*$", text or "")
    if not matches:
        return 1
    return max(int(x) for x in matches)


# -------------------------
# Ekstrakcja z JSON spark-state
# -------------------------


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


def extract_promo_web(offer):
    web = get_nested(offer, "promotionPricesSalesChannel", "web") or {}
    return (
        web.get("_for_action_price")
        or web.get("ForActionPrice")
        or web.get("forActionPrice")
        or {}
    )


def extract_offer_from_json(offer, checked_at, code_map):
    product_id = offer.get("product_id") or offer.get("productId")
    promo = extract_promo_web(offer)
    code_price = get_nested(promo, "code_price", "amount") or get_nested(promo, "codePrice", "amount")
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
        "promo_date_from": promo.get("date_from") or promo.get("dateFrom"),
        "promo_date_to": promo.get("date_to") or promo.get("dateTo"),
        "omnibus_price_web": grosze_to_pln(offer.get("omnibus_price_web") or offer.get("omnibusPriceWeb")),
        "availability": availability.get("display_name") or availability.get("displayName"),
        "availability_type": availability.get("type"),
        "source": "spark-state",
    }


# -------------------------
# Awaryjna ekstrakcja z tekstu wyrenderowanej strony
# -------------------------


def extract_product_links(page):
    links = {}
    try:
        anchors = page.eval_on_selector_all(
            "a[href]",
            """
            els => els.map(a => ({
                text: (a.innerText || a.textContent || '').trim().replace(/\s+/g, ' '),
                href: a.href
            }))
            """,
        )
    except Exception:
        return links

    for item in anchors:
        text = item.get("text") or ""
        href = item.get("href") or ""
        code = extract_lego_code_from_name(text)
        if text.startswith("LEGO ") and code and href:
            links.setdefault(code, href)
            links.setdefault(text, href)

    return links


def find_price_after(lines, start_index, max_lines=8):
    for line in lines[start_index + 1 : start_index + 1 + max_lines]:
        price = price_text_to_float(line)
        if price is not None:
            return price
    return None


def find_price_before_marker(block, marker):
    for i, line in enumerate(block):
        if marker in line:
            for previous in reversed(block[max(0, i - 8) : i]):
                price = price_text_to_float(previous)
                if price is not None:
                    return price
    return None


def find_promo_date_text(block):
    for line in block:
        if "Cena z kodem obowiązuje" in line:
            return line
    return None


def parse_products_from_text(text, checked_at, product_links):
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    starts = []
    seen_start_indexes = set()

    for i, line in enumerate(lines):
        if line.startswith("LEGO ") and re.search(r"\b\d{5}\b", line):
            # Czasem tytuł może wystąpić podwójnie blisko siebie. Nie bierzemy bliźniaka z okolicy.
            if any(abs(i - old) < 3 for old in seen_start_indexes):
                continue
            starts.append(i)
            seen_start_indexes.add(i)

    rows = []
    seen_codes = set()

    for pos, start in enumerate(starts):
        end = starts[pos + 1] if pos + 1 < len(starts) else len(lines)
        block = lines[start:end]
        name = block[0]
        joined = "\n".join(block)

        code_match = re.search(r"Kod producenta:\s*(\d{5})", joined)
        lego_code = code_match.group(1) if code_match else extract_lego_code_from_name(name)

        if not lego_code or lego_code in seen_codes:
            continue
        seen_codes.add(lego_code)

        promo_code = None
        price_with_code = None
        for i, line in enumerate(block):
            promo_match = re.search(r"Cena z kodem:\s*([A-Z0-9\-]+)", line)
            if promo_match:
                promo_code = promo_match.group(1)
                price_with_code = find_price_after(block, i)
                break

        price_gross = find_price_before_marker(block, "Cena przed kodem")
        omnibus_price = find_price_before_marker(block, "Najniższa cena z 30 dni")

        internal_code = None
        internal_code_match = re.search(r"Kod:\s*(\d+)", joined)
        if internal_code_match:
            internal_code = internal_code_match.group(1)

        availability = None
        if "Do koszyka" in joined:
            availability = "Dostępny"
        elif "Powiadom mnie" in joined:
            availability = "Niedostępny"

        rows.append(
            {
                "checked_at": checked_at,
                "shop": "Media Expert",
                "lego_code": lego_code,
                "offer_id": internal_code,
                "product_id": None,
                "name": name,
                "url": product_links.get(lego_code) or product_links.get(name),
                "price_gross": price_gross,
                "price_with_code": price_with_code,
                "promo_code": promo_code,
                "promo_date_from": None,
                "promo_date_to": find_promo_date_text(block),
                "omnibus_price_web": omnibus_price,
                "availability": availability,
                "availability_type": None,
                "source": "rendered-page-text",
            }
        )

    return rows


# -------------------------
# Playwright: pobranie strony
# -------------------------


def save_debug_files(page):
    try:
        DEBUG_HTML_FILE.write_text(page.content(), encoding="utf-8")
    except Exception:
        pass

    try:
        DEBUG_TEXT_FILE.write_text(page.locator("body").inner_text(timeout=5000), encoding="utf-8")
    except Exception:
        pass


def capture_page(page, url, checked_at):
    spark_states = []

    def on_response(response):
        if "/spark-state/" in response.url:
            print(f"Złapano response spark-state: {response.status} {response.url}")
            if response.status == 200:
                try:
                    spark_states.append(response.json())
                except Exception as exc:
                    print(f"Nie udało się odczytać JSON spark-state: {exc}")

    page.on("response", on_response)

    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=90000)
        print(f"Otwieram: {url}")
        if response:
            print(f"Status strony: {response.status}")
    except PlaywrightTimeoutError:
        print(f"Timeout przy otwieraniu: {url}")

    # Dajemy stronie chwilę na dogranie danych.
    for _ in range(6):
        if spark_states:
            break
        page.wait_for_timeout(3000)

    if spark_states:
        state = spark_states[0]
        code_map = extract_code_map(state)
        offers = get_loaded_offers(state)
        rows = [extract_offer_from_json(offer, checked_at, code_map) for offer in offers]
        total_pages = get_total_pages_from_state(state)
        return rows, total_pages, "spark-state"

    # Jeżeli nie było response /spark-state/, sprawdzamy, czy link jest w HTML.
    html = page.content()
    match = re.search(r"/spark-state/[a-zA-Z0-9\-]+", html)
    if match:
        spark_url = BASE_URL + match.group(0)
        print(f"Znaleziono spark-state w HTML: {spark_url}")
        try:
            response = page.request.get(spark_url, headers={"user-agent": USER_AGENT, "referer": url})
            print(f"Status spark-state z HTML: {response.status}")
            if response.status == 200:
                state = response.json()
                code_map = extract_code_map(state)
                offers = get_loaded_offers(state)
                rows = [extract_offer_from_json(offer, checked_at, code_map) for offer in offers]
                total_pages = get_total_pages_from_state(state)
                return rows, total_pages, "spark-state-from-html"
        except Exception as exc:
            print(f"Nie udało się pobrać spark-state z HTML: {exc}")

    # Ostatnia furtka: strona bywa wyrenderowana bez spark-state. Czytamy widoczny tekst.
    body_text = page.locator("body").inner_text(timeout=15000)
    product_links = extract_product_links(page)
    rows = parse_products_from_text(body_text, checked_at, product_links)
    total_pages = get_total_pages_from_text(body_text)

    print(f"Fallback tekstowy: znaleziono {len(rows)} produktów")

    if not rows:
        save_debug_files(page)
        print("Pierwsze 1000 znaków strony:")
        print(body_text[:1000])

    return rows, total_pages, "rendered-page-text"


def collect_all_products():
    checked_at = datetime.now(ZoneInfo("Europe/Warsaw")).isoformat(timespec="seconds")
    rows = []
    seen_keys = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
        )
        context = browser.new_context(
            locale="pl-PL",
            timezone_id="Europe/Warsaw",
            viewport={"width": 1365, "height": 1200},
            user_agent=USER_AGENT,
            extra_http_headers={
                "accept-language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )
        page = context.new_page()

        first_rows, total_pages, source = capture_page(page, get_page_url(1), checked_at)
        print(f"Źródło danych strony 1: {source}")
        print(f"Strona 1: {len(first_rows)} produktów")

        for row in first_rows:
            key = row.get("product_id") or row.get("lego_code") or row.get("name")
            if key and key not in seen_keys:
                seen_keys.add(key)
                rows.append(row)

        print(f"Liczba stron według strony: {total_pages}")

        for page_number in range(2, total_pages + 1):
            page_rows, _, page_source = capture_page(page, get_page_url(page_number), checked_at)
            print(f"Źródło danych strony {page_number}: {page_source}")
            print(f"Strona {page_number}: {len(page_rows)} produktów")

            for row in page_rows:
                key = row.get("product_id") or row.get("lego_code") or row.get("name")
                if key and key not in seen_keys:
                    seen_keys.add(key)
                    rows.append(row)

        browser.close()

    return rows


# -------------------------
# Zapis danych
# -------------------------


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
        raise RuntimeError("Nie pobrano produktów. Zobacz debug_page.html/debug_page.txt w artifactach workflow.")

    save_latest(rows)
    append_history(rows)
    print(f"Zapisano produktów: {len(rows)}")


if __name__ == "__main__":
    main()
