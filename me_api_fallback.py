import json
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import requests

import run_apify

API_URL = "https://www.mediaexpert.pl/api/graphql/product-offer/query/3760563353"
REFERER = "https://www.mediaexpert.pl/zabawki/lego/lego/promocje_cena-z-kodem?limit=50"
MIN_UPDATED_ROWS = 20

HEADERS = {
    "accept": "application/vnd.enp.api+json;version=v1",
    "accept-language": "pl-PL,pl;q=0.9,en-US;q=0.8,en;q=0.7",
    "content-type": "application/json",
    "content-website": "4",
    "referer": REFERER,
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/149.0.0.0 Safari/537.36"
    ),
    "x-legacy-offers-mode": "1",
    "x-spark": "hybrid",
}


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def dig(obj: Any, *keys: str) -> Any:
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return None
        if key in cur:
            cur = cur[key]
        else:
            return None
    return cur


def money_to_pln(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        for key in ("amount", "value", "price", "gross"):
            if key in value:
                return money_to_pln(value[key])
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    # Media Expert zwykle trzyma ceny w groszach, np. 74371 = 743.71 zl.
    if number >= 1000:
        return round(number / 100, 2)
    return round(number, 2)


def get_offer_from_payload(payload: dict[str, Any]) -> dict[str, Any] | None:
    data = payload.get("data") if isinstance(payload, dict) else None
    by_id = data.get("byId") if isinstance(data, dict) else None
    if isinstance(by_id, list) and by_id:
        return by_id[0]
    if isinstance(by_id, dict):
        return by_id
    return None


def build_query(product_id: str) -> str:
    return f'''
query QuerySimpleProductOfferByProduct {{
  byId(identifierName:"productId", identifierValues:["{product_id}"]) {{
    id
    productId
    productParentId
    name
    link
    price {{
      priceGross
      omnibusPriceApp
      omnibusPriceWeb
      promoPrice {{
        clubPrice
        forActionPrice
        forActionCode
        dateFrom
        dateTo
      }}
    }}
    promotionPricesSalesChannel {{
      web {{
        ForActionPrice {{
          codePrice {{ amount currency }}
          code
          dateFrom
          dateTo
        }}
      }}
      app {{
        ForActionPrice {{
          codePrice {{ amount currency }}
          code
          dateFrom
          dateTo
        }}
      }}
    }}
    availability {{
      id
      name
      displayName
      type
    }}
    availableInStore
  }}
}}
'''.strip()


def fetch_offer(product_id: int | str) -> dict[str, Any] | None:
    query = build_query(str(product_id))
    url = f"{API_URL}?{urlencode({'query': query})}"
    response = requests.get(url, headers=HEADERS, timeout=45)
    print(f"Product API {product_id}: HTTP {response.status_code}")
    if response.status_code >= 400:
        print(response.text[:500])
        return None
    try:
        payload = response.json()
    except ValueError:
        print(response.text[:500])
        return None
    if payload.get("errors"):
        print(json.dumps(payload.get("errors"), ensure_ascii=False)[:1000])
        return None
    return get_offer_from_payload(payload)


def update_row_from_offer(row: dict[str, Any], offer: dict[str, Any], checked_at: str) -> dict[str, Any]:
    out = dict(row)
    price = offer.get("price") or {}
    promo = price.get("promoPrice") or {}
    web_action = dig(offer, "promotionPricesSalesChannel", "web", "ForActionPrice") or {}
    app_action = dig(offer, "promotionPricesSalesChannel", "app", "ForActionPrice") or {}
    action = web_action or app_action or {}
    availability = offer.get("availability") or {}

    out["checked_at"] = checked_at
    out["product_id"] = offer.get("productId") or out.get("product_id")
    out["product_parent_id"] = offer.get("productParentId") or out.get("product_parent_id")
    out["offer_id"] = offer.get("id") or out.get("offer_id")
    out["name"] = offer.get("name") or out.get("name")

    link = offer.get("link") or out.get("url")
    if isinstance(link, str) and link.startswith("/"):
        out["url"] = "https://www.mediaexpert.pl" + link
    elif link:
        out["url"] = link

    price_gross = money_to_pln(price.get("priceGross"))
    if price_gross is not None:
        out["price_gross"] = price_gross

    code_price = money_to_pln(dig(action, "codePrice", "amount"))
    if code_price is None:
        code_price = money_to_pln(promo.get("forActionPrice"))
    out["price_with_code"] = code_price

    out["promo_code"] = action.get("code") or promo.get("forActionCode") or None
    out["promo_date_from"] = action.get("dateFrom") or promo.get("dateFrom") or None
    out["promo_date_to"] = action.get("dateTo") or promo.get("dateTo") or None

    omnibus_web = money_to_pln(price.get("omnibusPriceWeb"))
    omnibus_app = money_to_pln(price.get("omnibusPriceApp"))
    if omnibus_web is not None:
        out["omnibus_price_web"] = omnibus_web
    if omnibus_app is not None:
        out["omnibus_price_app"] = omnibus_app

    out["availability"] = availability.get("displayName") or availability.get("name") or out.get("availability")
    out["availability_type"] = availability.get("type") or out.get("availability_type")
    if "availableInStore" in offer:
        out["available_in_store"] = offer.get("availableInStore")

    return out


def load_latest_rows() -> list[dict[str, Any]]:
    rows = json.loads(run_apify.LATEST_FILE.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise RuntimeError("latest_prices.json nie jest lista")
    return [row for row in rows if isinstance(row, dict)]


def refresh_known_products_from_api() -> list[dict[str, Any]]:
    rows = load_latest_rows()
    checked_at = now_utc_iso()
    refreshed_rows = []
    updated = 0

    for index, row in enumerate(rows, start=1):
        product_id = row.get("product_id")
        if not product_id:
            refreshed_rows.append(dict(row))
            continue
        offer = fetch_offer(product_id)
        if offer:
            refreshed_rows.append(update_row_from_offer(row, offer, checked_at))
            updated += 1
        else:
            fallback_row = dict(row)
            fallback_row["checked_at"] = checked_at
            refreshed_rows.append(fallback_row)
        time.sleep(0.25)
        print(f"Product API progress: {index}/{len(rows)}, updated={updated}")

    if updated < MIN_UPDATED_ROWS:
        raise RuntimeError(f"Fallback Product API odswiezyl tylko {updated} produktow, minimum to {MIN_UPDATED_ROWS}.")

    run_apify.guard_against_partial_result(refreshed_rows)
    refreshed_rows = run_apify.enrich_tracking_dates(refreshed_rows)
    run_apify.save_latest(refreshed_rows)
    run_apify.append_history(refreshed_rows)
    print(f"Fallback Product API zapisany: {len(refreshed_rows)} produktow, realnie odswiezonych: {updated}")
    return refreshed_rows


if __name__ == "__main__":
    refresh_known_products_from_api()
