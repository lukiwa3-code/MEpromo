import csv
import json
import os
import time
from datetime import datetime
from pathlib import Path

import requests


ACTOR_ID = "apify~playwright-scraper"
APIFY_BASE_URL = "https://api.apify.com/v2"

LISTING_URL = "https://www.mediaexpert.pl/zabawki/lego/lego/promocje_cena-z-kodem?limit=50"
DATA_DIR = Path("data")
LATEST_FILE = DATA_DIR / "latest_prices.json"
HISTORY_FILE = DATA_DIR / "price_history.csv"


PAGE_FUNCTION = r"""
async function pageFunction(context) {
    const { page, response, log } = context;
    const checkedAt = new Date().toISOString();

    function getStatus(responseObject) {
        if (!responseObject) return null;
        if (typeof responseObject.status === 'function') return responseObject.status();
        if (responseObject.status !== undefined) return responseObject.status;
        if (responseObject.statusCode !== undefined) return responseObject.statusCode;
        return null;
    }

    function groszeToPln(value) {
        if (value === undefined || value === null || value === '') return null;
        const numberValue = Number(value);
        if (Number.isNaN(numberValue)) return null;
        return Math.round(numberValue) / 100;
    }

    function getNested(object, path) {
        let current = object;
        for (const key of path) {
            if (!current || typeof current !== 'object') return null;
            current = current[key];
        }
        return current;
    }

    function extractLegoCodeFromName(name) {
        if (!name) return null;
        const match = String(name).match(/\b(\d{5})\b/);
        return match ? match[1] : null;
    }

    function extractCodeMap(state) {
        const result = {};
        const additionals = getNested(state, [
            'ProductList:ProductListAdditionalService.state',
            'offerAdditionals',
        ]) || [];

        for (const item of additionals) {
            const variants = item.variants || [];
            for (const variant of variants) {
                const offers = variant.offers || {};
                for (const [legoCode, productData] of Object.entries(offers)) {
                    if (productData && productData.product_id) {
                        result[String(productData.product_id)] = String(legoCode);
                    }
                }
            }
        }
        return result;
    }

    function extractRowsFromState(state) {
        const productState = state['Service:GenericProductListService.state'] || {};
        const offers = Array.isArray(productState.loadedOffers) ? productState.loadedOffers : [];
        const codeMap = extractCodeMap(state);

        return offers.map((offer) => {
            const productId = offer.product_id || offer.productId;
            const productIdText = productId === undefined || productId === null ? '' : String(productId);
            const promoWeb = getNested(offer, [
                'promotionPricesSalesChannel',
                'web',
                '_for_action_price',
            ]) || getNested(offer, [
                'promotionPricesSalesChannel',
                'web',
                'ForActionPrice',
            ]) || {};

            const codePrice = promoWeb.code_price || promoWeb.codePrice || {};
            const link = offer.link || '';
            const fullUrl = link.startsWith('/') ? `https://www.mediaexpert.pl${link}` : link;
            const availability = offer.availability || {};
            const name = offer.name || null;

            return {
                checked_at: checkedAt,
                shop: 'Media Expert',
                lego_code: codeMap[productIdText] || extractLegoCodeFromName(name),
                offer_id: offer.id || null,
                product_id: productId || null,
                product_parent_id: offer.product_parent_id || offer.productParentId || null,
                name,
                url: fullUrl,
                price_gross: groszeToPln(offer.price_gross ?? offer.priceGross),
                price_with_code: groszeToPln(codePrice.amount),
                promo_code: promoWeb.code || null,
                promo_date_from: promoWeb.date_from || promoWeb.dateFrom || null,
                promo_date_to: promoWeb.date_to || promoWeb.dateTo || null,
                omnibus_price_web: groszeToPln(offer.omnibus_price_web ?? offer.omnibusPriceWeb),
                omnibus_price_app: groszeToPln(offer.omnibus_price_app ?? offer.omnibusPriceApp),
                availability: availability.display_name || availability.displayName || null,
                availability_type: availability.type || null,
                available_in_store: offer.available_in_store ?? offer.availableInStore ?? null,
            };
        });
    }

    function getProductStateInfo(state) {
        const productState = state['Service:GenericProductListService.state'] || {};
        const loadedOffers = Array.isArray(productState.loadedOffers) ? productState.loadedOffers : [];
        return {
            loadedOffersCount: loadedOffers.length,
            offersCount: productState.offersCount || null,
            totalPages: productState.totalPages || null,
            currentPage: productState.currentPage || null,
        };
    }

    async function getBodyText() {
        return await page.locator('body').innerText({ timeout: 15000 }).catch(() => '');
    }

    async function fetchSparkState(sparkUrl) {
        return await page.evaluate(async (url) => {
            const response = await fetch(url, { credentials: 'include' });
            if (!response.ok) {
                throw new Error(`Spark-state HTTP ${response.status}`);
            }
            return await response.json();
        }, sparkUrl);
    }

    function unique(values) {
        return [...new Set(values.filter(Boolean))];
    }

    async function collectSparkUrls() {
        const performanceUrls = await page.evaluate(() =>
            performance
                .getEntriesByType('resource')
                .map((entry) => entry.name)
                .filter((url) => url.includes('/spark-state/'))
        ).catch(() => []);

        const html = await page.content();
        const htmlMatches = [...html.matchAll(/\/spark-state\/[a-zA-Z0-9\-]+/g)]
            .map((match) => new URL(match[0], 'https://www.mediaexpert.pl').href);

        return unique([...performanceUrls, ...htmlMatches]);
    }

    async function chooseBestSparkState(sparkUrls) {
        const candidates = [];

        for (const sparkUrl of sparkUrls) {
            try {
                const state = await fetchSparkState(sparkUrl);
                const rows = extractRowsFromState(state);
                const info = getProductStateInfo(state);

                log.info(
                    `Spark candidate: rows=${rows.length}, loadedOffers=${info.loadedOffersCount}, ` +
                    `offersCount=${info.offersCount}, totalPages=${info.totalPages}, url=${sparkUrl}`
                );

                candidates.push({
                    source: 'spark-state',
                    sparkUrl,
                    state,
                    rows,
                    ...info,
                });
            } catch (error) {
                log.info(`Nie udało się pobrać lub odczytać spark-state ${sparkUrl}: ${error.message}`);
            }
        }

        candidates.sort((a, b) => {
            if (b.rows.length !== a.rows.length) return b.rows.length - a.rows.length;
            return (b.offersCount || 0) - (a.offersCount || 0);
        });

        return candidates[0] || null;
    }

    await page.waitForLoadState('domcontentloaded', { timeout: 90000 }).catch(() => {});
    await page.waitForLoadState('networkidle', { timeout: 25000 }).catch(() => {});
    await page.waitForTimeout(10000);

    const status = getStatus(response);
    const bodyText = await getBodyText();

    log.info(`Media Expert status: ${status}`);
    log.info(`Body preview: ${bodyText.slice(0, 300).replace(/\s+/g, ' ')}`);

    if (
        status === 403 ||
        bodyText.includes('nie jesteś robotem') ||
        bodyText.includes('Dbamy o Twoje bezpieczeństwo')
    ) {
        return {
            source: 'blocked',
            status,
            count: 0,
            rows: [],
            pageText: bodyText.slice(0, 2500),
        };
    }

    const sparkUrls = await collectSparkUrls();
    log.info(`Spark-state candidate count: ${sparkUrls.length}`);

    const best = await chooseBestSparkState(sparkUrls);

    if (!best) {
        return {
            source: 'no-spark-state',
            status,
            count: 0,
            rows: [],
            pageText: bodyText.slice(0, 2500),
        };
    }

    return {
        source: best.source,
        sparkUrl: best.sparkUrl,
        status,
        count: best.rows.length,
        offersCount: best.offersCount || null,
        totalPages: best.totalPages || null,
        currentPage: best.currentPage || null,
        rows: best.rows,
    };
}
"""


def get_apify_token():
    token = os.environ.get("APIFY_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Brakuje sekretu APIFY_TOKEN. Dodaj go w GitHub: "
            "Settings -> Secrets and variables -> Actions -> New repository secret."
        )
    return token


def auth_headers():
    return {
        "Authorization": f"Bearer {get_apify_token()}",
        "Content-Type": "application/json",
    }


def build_proxy_configuration():
    proxy = {"useApifyProxy": True}

    groups_raw = os.environ.get("APIFY_PROXY_GROUPS", "").strip()
    if groups_raw:
        proxy["apifyProxyGroups"] = [group.strip() for group in groups_raw.split(",") if group.strip()]

    country_code = os.environ.get("APIFY_PROXY_COUNTRY", "").strip()
    if country_code:
        proxy["apifyProxyCountry"] = country_code

    return proxy


def build_actor_input():
    return {
        "startUrls": [{"url": LISTING_URL}],
        "maxRequestsPerCrawl": 1,
        "maxConcurrency": 1,
        "pageFunction": PAGE_FUNCTION,
        "proxyConfiguration": build_proxy_configuration(),
        "browserLog": False,
        "debugLog": True,
        "navigationTimeoutSecs": 120,
        "requestHandlerTimeoutSecs": 180,
        "useChrome": True,
    }


def parse_apify_json_response(response, action_name):
    text = response.text or ""
    content_type = response.headers.get("content-type", "")

    print(f"{action_name}: HTTP {response.status_code}, content-type: {content_type}")

    if response.status_code >= 400:
        print(text[:5000])
        response.raise_for_status()

    if not text.strip():
        raise RuntimeError(
            f"Apify zwrócił pustą odpowiedź przy akcji: {action_name}. "
            f"HTTP {response.status_code}, headers: {dict(response.headers)}"
        )

    try:
        return response.json()
    except ValueError as exc:
        raise RuntimeError(
            f"Apify nie zwrócił JSON przy akcji: {action_name}. "
            f"HTTP {response.status_code}, content-type: {content_type}, "
            f"pierwsze 2000 znaków: {text[:2000]}"
        ) from exc


def apify_post_json(url, payload, action_name, timeout=120):
    response = requests.post(
        url,
        headers=auth_headers(),
        json=payload,
        timeout=timeout,
    )
    return parse_apify_json_response(response, action_name)


def apify_get_json(url, action_name, timeout=120):
    response = requests.get(url, headers=auth_headers(), timeout=timeout)
    return parse_apify_json_response(response, action_name)


def start_actor_run():
    url = f"{APIFY_BASE_URL}/acts/{ACTOR_ID}/runs"
    payload = apify_post_json(url, build_actor_input(), "start_actor_run", timeout=120)
    return payload.get("data", payload)


def get_actor_run(run_id):
    url = f"{APIFY_BASE_URL}/actor-runs/{run_id}"
    payload = apify_get_json(url, "get_actor_run", timeout=120)
    return payload.get("data", payload)


def wait_for_actor_run(run_id, max_wait_seconds=600):
    terminal_statuses = {"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"}
    start = time.time()

    while True:
        run = get_actor_run(run_id)
        status = run.get("status")
        print(f"Apify status: {status}")

        if status in terminal_statuses:
            return run

        if time.time() - start > max_wait_seconds:
            raise RuntimeError(f"Apify run nie zakończył się w czasie {max_wait_seconds}s. Run ID: {run_id}")

        time.sleep(10)


def fetch_dataset_items(dataset_id):
    url = f"{APIFY_BASE_URL}/datasets/{dataset_id}/items?format=json&clean=false"
    payload = apify_get_json(url, "fetch_dataset_items", timeout=120)
    return payload


def fetch_run_log(run_id):
    url = f"{APIFY_BASE_URL}/logs/{run_id}"
    response = requests.get(url, headers=auth_headers(), timeout=120)

    if response.status_code >= 400:
        return f"Nie udało się pobrać logu Apify. HTTP {response.status_code}: {response.text[:1000]}"

    return response.text


def call_apify():
    print("Startuję Actor w Apify przez endpoint /runs...")
    run = start_actor_run()

    run_id = run.get("id")
    if not run_id:
        raise RuntimeError(f"Apify nie zwrócił ID uruchomienia. Run: {run}")

    print(f"Apify run ID: {run_id}")
    print(f"Apify initial status: {run.get('status')}")

    final_run = wait_for_actor_run(run_id)

    print(f"Apify final status: {final_run.get('status')}")
    print(f"Apify defaultDatasetId: {final_run.get('defaultDatasetId')}")

    log_text = fetch_run_log(run_id)
    print("--- Apify log preview ---")
    print(log_text[-9000:])
    print("--- end Apify log preview ---")

    if final_run.get("status") != "SUCCEEDED":
        raise RuntimeError(f"Apify run nie zakończył się sukcesem. Status: {final_run.get('status')}")

    dataset_id = final_run.get("defaultDatasetId")
    if not dataset_id:
        raise RuntimeError(f"Apify nie zwrócił defaultDatasetId. Run: {final_run}")

    return fetch_dataset_items(dataset_id)


def normalize_rows(apify_items):
    if not isinstance(apify_items, list) or not apify_items:
        raise RuntimeError(f"Apify nie zwrócił wyników w dataset. Odpowiedź: {apify_items}")

    first_item = apify_items[0]

    if first_item.get("#error"):
        raise RuntimeError(
            "Apify pageFunction zakończył się błędem. Rekord błędu: "
            + json.dumps(first_item, ensure_ascii=False)[:5000]
        )

    rows = first_item.get("rows") or []
    offers_count = first_item.get("offersCount")

    print(f"Źródło Apify: {first_item.get('source')}")
    print(f"Status strony: {first_item.get('status')}")
    print(f"Liczba produktów: {len(rows)}")
    print(f"offersCount ze strony: {offers_count}")

    if first_item.get("sparkUrl"):
        print(f"Spark-state: {first_item.get('sparkUrl')}")

    if not rows:
        page_text = first_item.get("pageText") or ""
        print("Pierwszy tekst strony z Apify:")
        print(page_text[:2500])
        raise RuntimeError(
            "Apify uruchomił scraper, ale nie pobrał produktów. "
            "Jeśli source=blocked, trzeba w Apify włączyć lepszy proxy, np. residential."
        )

    if offers_count and len(rows) < min(20, int(float(offers_count) * 0.5)):
        raise RuntimeError(
            f"Scraper pobrał podejrzanie mało produktów: {len(rows)} z offersCount={offers_count}. "
            "Nie zapisuję danych, żeby nie nadpisać pełnej tabeli niepełnym wynikiem."
        )

    return rows


def product_key(row):
    for field in ("lego_code", "product_id", "offer_id", "url", "name"):
        value = row.get(field)
        if value not in (None, ""):
            return f"{field}:{value}"
    return ""


def price_value(value):
    if value in (None, ""):
        return None
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def price_signature(row):
    return (
        price_value(row.get("price_gross")),
        price_value(row.get("price_with_code")),
    )


def load_previous_latest():
    if not LATEST_FILE.exists():
        return {}

    try:
        previous_rows = json.loads(LATEST_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Nie udało się odczytać poprzedniego latest_prices.json: {exc}")
        return {}

    if not isinstance(previous_rows, list):
        return {}

    result = {}
    for row in previous_rows:
        if isinstance(row, dict):
            key = product_key(row)
            if key:
                result[key] = row
    return result


def load_history_first_seen():
    if not HISTORY_FILE.exists() or HISTORY_FILE.stat().st_size == 0:
        return {}

    first_seen_by_key = {}
    try:
        with HISTORY_FILE.open("r", newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            for row in reader:
                key = product_key(row)
                checked_at = row.get("checked_at")
                if key and checked_at and key not in first_seen_by_key:
                    first_seen_by_key[key] = checked_at
    except OSError as exc:
        print(f"Nie udało się odczytać historii do first_seen: {exc}")

    return first_seen_by_key


def get_history_last_batch_count():
    if not HISTORY_FILE.exists() or HISTORY_FILE.stat().st_size == 0:
        return 0

    last_checked_at = None
    count = 0

    try:
        with HISTORY_FILE.open("r", newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            for row in reader:
                checked_at = row.get("checked_at")
                if not checked_at:
                    continue
                if checked_at != last_checked_at:
                    last_checked_at = checked_at
                    count = 1
                else:
                    count += 1
    except OSError as exc:
        print(f"Nie udało się policzyć ostatniej paczki historii: {exc}")
        return 0

    return count


def guard_against_partial_result(rows):
    history_last_count = get_history_last_batch_count()

    if history_last_count >= 20 and len(rows) < max(10, int(history_last_count * 0.7)):
        raise RuntimeError(
            f"Scraper pobrał tylko {len(rows)} produktów, a ostatnia pełna paczka w historii miała "
            f"{history_last_count}. Nie zapisuję danych, żeby nie skurczyć tabeli."
        )


def enrich_tracking_dates(rows):
    previous_by_key = load_previous_latest()
    history_first_seen = load_history_first_seen()
    new_count = 0
    changed_count = 0

    for row in rows:
        key = product_key(row)
        previous = previous_by_key.get(key)
        checked_at = row.get("checked_at") or ""

        if previous:
            row["first_seen_at"] = (
                previous.get("first_seen_at")
                or history_first_seen.get(key)
                or previous.get("checked_at")
                or checked_at
            )
            old_signature = price_signature(previous)
            new_signature = price_signature(row)

            if old_signature != new_signature:
                row["price_changed_at"] = checked_at
                row["previous_price_gross"] = previous.get("price_gross")
                row["previous_price_with_code"] = previous.get("price_with_code")
                row["price_changed_now"] = True
                changed_count += 1
            else:
                row["price_changed_at"] = previous.get("price_changed_at")
                row["previous_price_gross"] = previous.get("previous_price_gross")
                row["previous_price_with_code"] = previous.get("previous_price_with_code")
                row["price_changed_now"] = False

            row["is_new_product"] = False
        else:
            row["first_seen_at"] = history_first_seen.get(key) or checked_at
            row["price_changed_at"] = None
            row["previous_price_gross"] = None
            row["previous_price_with_code"] = None
            row["price_changed_now"] = False
            row["is_new_product"] = key not in history_first_seen
            if row["is_new_product"]:
                new_count += 1

    print(f"Nowe produkty w tym odświeżeniu: {new_count}")
    print(f"Produkty ze zmianą ceny w tym odświeżeniu: {changed_count}")
    return rows


def save_latest(rows):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_FILE.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_fieldnames(existing_fieldnames, rows):
    fieldnames = []
    for field in existing_fieldnames:
        if field and field not in fieldnames:
            fieldnames.append(field)

    for row in rows:
        for field in row.keys():
            if field not in fieldnames:
                fieldnames.append(field)

    return fieldnames


def append_history(rows):
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    existing_rows = []
    existing_fieldnames = []

    if HISTORY_FILE.exists() and HISTORY_FILE.stat().st_size > 0:
        with HISTORY_FILE.open("r", newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            existing_fieldnames = reader.fieldnames or []
            existing_rows = list(reader)

    fieldnames = build_fieldnames(existing_fieldnames, rows)

    if not HISTORY_FILE.exists() or not existing_fieldnames or existing_fieldnames != fieldnames:
        with HISTORY_FILE.open("w", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(existing_rows)
            writer.writerows(rows)
    else:
        with HISTORY_FILE.open("a", newline="", encoding="utf-8") as file:
            writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
            writer.writerows(rows)


def main():
    print("Uruchamiam Apify Playwright Scraper...")
    apify_items = call_apify()
    rows = normalize_rows(apify_items)
    guard_against_partial_result(rows)
    rows = enrich_tracking_dates(rows)

    save_latest(rows)
    append_history(rows)

    print(f"Zapisano produktów: {len(rows)}")
    print(f"Plik aktualny: {LATEST_FILE}")
    print(f"Historia: {HISTORY_FILE}")


if __name__ == "__main__":
    main()
