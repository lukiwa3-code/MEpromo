import csv
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests


APIFY_ACTOR_URL = (
    "https://api.apify.com/v2/acts/apify~playwright-scraper/"
    "run-sync-get-dataset-items?format=json&clean=true&timeout=300"
)

LISTING_URL = "https://www.mediaexpert.pl/zabawki/lego/lego/promocje_cena-z-kodem?limit=50"
DATA_DIR = Path("data")
LATEST_FILE = DATA_DIR / "latest_prices.json"
HISTORY_FILE = DATA_DIR / "price_history.csv"


PAGE_FUNCTION = r"""
async function pageFunction(context) {
    const { page, request, response, log } = context;

    const checkedAt = new Date().toISOString();

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
        const offers = productState.loadedOffers || [];
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
                price_gross: groszeToPln(offer.price_gross || offer.priceGross),
                price_with_code: groszeToPln(codePrice.amount),
                promo_code: promoWeb.code || null,
                promo_date_from: promoWeb.date_from || promoWeb.dateFrom || null,
                promo_date_to: promoWeb.date_to || promoWeb.dateTo || null,
                omnibus_price_web: groszeToPln(offer.omnibus_price_web || offer.omnibusPriceWeb),
                omnibus_price_app: groszeToPln(offer.omnibus_price_app || offer.omnibusPriceApp),
                availability: availability.display_name || availability.displayName || null,
                availability_type: availability.type || null,
                available_in_store: offer.available_in_store || offer.availableInStore || null,
            };
        });
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

    await page.waitForLoadState('domcontentloaded', { timeout: 90000 }).catch(() => {});
    await page.waitForTimeout(9000);

    const status = response ? response.status() : null;
    const bodyText = await page.locator('body').innerText({ timeout: 10000 }).catch(() => '');

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
            pageText: bodyText.slice(0, 1500),
        };
    }

    let sparkUrl = null;
    let state = null;

    const resourceUrls = await page.evaluate(() =>
        performance
            .getEntriesByType('resource')
            .map((entry) => entry.name)
            .filter((url) => url.includes('/spark-state/'))
    ).catch(() => []);

    if (resourceUrls.length > 0) {
        sparkUrl = resourceUrls[resourceUrls.length - 1];
        try {
            state = await fetchSparkState(sparkUrl);
        } catch (error) {
            log.warning(`Nie udało się pobrać spark-state z performance: ${error.message}`);
        }
    }

    if (!state) {
        const html = await page.content();
        const match = html.match(/\/spark-state\/[a-zA-Z0-9\-]+/);
        if (match) {
            sparkUrl = new URL(match[0], 'https://www.mediaexpert.pl').href;
            try {
                state = await fetchSparkState(sparkUrl);
            } catch (error) {
                log.warning(`Nie udało się pobrać spark-state z HTML: ${error.message}`);
            }
        }
    }

    if (!state) {
        return {
            source: 'no-spark-state',
            status,
            count: 0,
            rows: [],
            pageText: bodyText.slice(0, 1500),
        };
    }

    const rows = extractRowsFromState(state);
    const productState = state['Service:GenericProductListService.state'] || {};

    return {
        source: 'spark-state',
        sparkUrl,
        status,
        count: rows.length,
        offersCount: productState.offersCount || null,
        totalPages: productState.totalPages || null,
        rows,
    };
}
"""


def build_actor_input():
    return {
        "startUrls": [{"url": LISTING_URL}],
        "maxRequestsPerCrawl": 1,
        "maxConcurrency": 1,
        "pageFunction": PAGE_FUNCTION,
        "proxyConfiguration": {
            "useApifyProxy": True,
        },
        "browserLog": False,
        "debugLog": True,
        "navigationTimeoutSecs": 120,
    }


def call_apify():
    token = os.environ.get("APIFY_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Brakuje sekretu APIFY_TOKEN. Dodaj go w GitHub: "
            "Settings -> Secrets and variables -> Actions -> New repository secret."
        )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    response = requests.post(
        APIFY_ACTOR_URL,
        headers=headers,
        json=build_actor_input(),
        timeout=380,
    )

    if response.status_code >= 400:
        print(response.text[:4000])
        response.raise_for_status()

    return response.json()


def normalize_rows(apify_items):
    if not isinstance(apify_items, list) or not apify_items:
        raise RuntimeError(f"Apify nie zwrócił wyników. Odpowiedź: {apify_items}")

    first_item = apify_items[0]
    rows = first_item.get("rows") or []

    print(f"Źródło Apify: {first_item.get('source')}")
    print(f"Status strony: {first_item.get('status')}")
    print(f"Liczba produktów: {len(rows)}")

    if first_item.get("sparkUrl"):
        print(f"Spark-state: {first_item.get('sparkUrl')}")

    if not rows:
        page_text = first_item.get("pageText") or ""
        print("Pierwszy tekst strony z Apify:")
        print(page_text[:1500])
        raise RuntimeError(
            "Apify uruchomił scraper, ale nie pobrał produktów. "
            "Jeśli source=blocked, trzeba w Apify włączyć lepszy proxy/residential."
        )

    return rows


def save_latest(rows):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LATEST_FILE.write_text(
        json.dumps(rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def append_history(rows):
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    file_exists = HISTORY_FILE.exists()

    with HISTORY_FILE.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=list(rows[0].keys()))

        if not file_exists:
            writer.writeheader()

        writer.writerows(rows)


def main():
    print("Uruchamiam Apify Playwright Scraper...")
    apify_items = call_apify()
    rows = normalize_rows(apify_items)

    save_latest(rows)
    append_history(rows)

    print(f"Zapisano produktów: {len(rows)}")
    print(f"Plik aktualny: {LATEST_FILE}")
    print(f"Historia: {HISTORY_FILE}")


if __name__ == "__main__":
    main()
