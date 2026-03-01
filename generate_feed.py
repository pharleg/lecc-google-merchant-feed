#!/usr/bin/env python3
"""
Lake Erie Clothing Company - Google Merchant Center Feed Generator
Pulls products from Wix Catalog V3 API and generates a Google-compliant TSV feed.

Collection membership is determined by querying each collection directly
using the filter on collectionId in the products query.
"""

import os
import csv
import json
import requests
import re
from datetime import datetime

# ── Config ──────────────────────────────────────────────────────────────────
API_KEY     = os.environ["WIX_API_KEY"]
SITE_ID     = os.environ["WIX_SITE_ID"]
ACCOUNT_ID  = os.environ["WIX_ACCOUNT_ID"]
STORE_URL   = "https://www.lakeerieclothing.com"
BRAND       = "Lake Erie Clothing Company"
OUTPUT_FILE = "google_feed.tsv"

HEADERS = {
    "Authorization":  API_KEY,
    "wix-site-id":    SITE_ID,
    "wix-account-id": ACCOUNT_ID,
    "Content-Type":   "application/json",
}

# ── Collection names → gender/age_group mapping ──────────────────────────────
# Keys must match Wix collection names exactly (case-insensitive comparison used below)
COLLECTION_GENDER_MAP = {
    "womens clothing": ("female", "adult"),
    "unisex clothing": ("unisex", "adult"),
    "lake living":     (None,      None),
}

# ── Load category map ────────────────────────────────────────────────────────
with open("category_map.json") as f:
    CAT_MAP = json.load(f)


# ── Wix API helpers ──────────────────────────────────────────────────────────

def get_collections():
    """
    Query collections using the stores/v1 endpoint with minimal body.
    Returns dict of collection_id -> normalized_name.
    """
    # Try the V1 endpoint with the exact format from Wix curl examples
    url = "https://www.wixapis.com/stores/v1/collections/query"
    body = {
        "query": {},
        "includeNumberOfProducts": False,
        "includeDescription": False
    }
    print(f"  Calling: POST {url}")
    r = requests.post(url, headers=HEADERS, json=body)
    print(f"  Collections API status: {r.status_code}")
    if not r.ok:
        print(f"  Collections API error: {r.text[:500]}")
        r.raise_for_status()
    data = r.json()
    collections = {}
    for c in data.get("collections", []):
        collections[c["id"]] = c.get("name", "").strip().lower()
    print(f"  Raw collections response keys: {list(data.keys())}")
    return collections


def query_products_by_collection(collection_id):
    """Query all products in a specific collection using V3 products API."""
    url = "https://www.wixapis.com/stores/v3/products/query"
    products = []
    offset = 0
    while True:
        body = {
            "query": {
                "filter": {"collections.id": {"$hasSome": [collection_id]}},
                "paging": {"limit": 100, "offset": offset}
            }
        }
        r = requests.post(url, headers=HEADERS, json=body)
        r.raise_for_status()
        data = r.json()
        batch = data.get("products", [])
        products.extend(batch)
        if len(batch) < 100:
            break
        offset += 100
    return products


def get_all_products_with_collections():
    """
    For each target collection, fetch its products.
    Returns list of (product, collection_name) tuples.
    """
    print("  Fetching collections...")
    collections = get_collections()
    print(f"  Found {len(collections)} total collections")
    for cid, cname in collections.items():
        print(f"    '{cname}' -> {cid}")

    results = []
    seen_ids = set()

    for cid, cname in collections.items():
        matched_target = None
        for target in COLLECTION_GENDER_MAP:
            if target in cname:
                matched_target = target
                break
        if not matched_target:
            continue

        print(f"  Fetching products for collection '{cname}'...")
        products = query_products_by_collection(cid)
        print(f"    Found {len(products)} products")
        for p in products:
            pid = p["id"]
            if pid not in seen_ids:
                seen_ids.add(pid)
                results.append((p, matched_target))

    return results


# ── Category helpers ─────────────────────────────────────────────────────────

def get_google_category(collection_name, product_name):
    title_lower = product_name.lower()

    if collection_name in ("womens clothing", "unisex clothing"):
        for kw, subcat in CAT_MAP["clothing_subcategory_keywords"].items():
            if kw in title_lower:
                return subcat
        return CAT_MAP["clothing_category"]

    if collection_name == "lake living":
        for kw, info in CAT_MAP["lake_living_keywords"].items():
            if kw in title_lower:
                return info["category"]
        return CAT_MAP["default_lake_living"]["category"]

    return CAT_MAP["clothing_category"]


# ── Feed row builder ──────────────────────────────────────────────────────────

def extract_option_value(variant, option_name):
    for choice in variant.get("choices", []):
        if choice.get("optionName", "").lower() == option_name.lower():
            return choice.get("description", "").strip()
    return ""


def clean_description(html_or_text):
    text = re.sub(r"<[^>]+>", " ", html_or_text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:5000]


def format_price(amount, currency="USD"):
    try:
        return f"{float(amount):.2f} {currency}"
    except (TypeError, ValueError):
        return ""


def get_price(product):
    """Extract price from whichever field the API version uses."""
    for path in [
        lambda p: (p["price"]["price"], p["price"].get("currency", "USD")),
        lambda p: (p["priceData"]["price"], p["priceData"].get("currency", "USD")),
        lambda p: (p["actualPriceRange"]["minValue"]["amount"], p["actualPriceRange"]["minValue"].get("currency", "USD")),
    ]:
        try:
            amount, currency = path(product)
            if amount:
                return format_price(amount, currency)
        except (KeyError, TypeError):
            pass
    return "0.00 USD"


def build_rows(product, collection_name):
    rows = []
    pid   = product.get("id", "")
    title = product.get("name", "").strip()
    description = clean_description(product.get("description", title))
    slug  = product.get("slug", "")
    product_url = f"{STORE_URL}/product-page/{slug}"

    # Images
    main_image = ""
    additional_images = []
    media = product.get("media", {})
    main_media = media.get("main", {})
    if main_media.get("image"):
        main_image = main_media["image"].get("url", "")
    for item in media.get("items", []):
        img_url = item.get("image", {}).get("url", "")
        if img_url and img_url != main_image:
            additional_images.append(img_url)

    gender, age_group = COLLECTION_GENDER_MAP.get(collection_name, (None, None))
    google_cat = get_google_category(collection_name, title)

    # Availability
    stock = product.get("stock", product.get("inventory", {}))
    in_stock = (
        stock.get("inStock", None) is True or
        stock.get("availabilityStatus", "IN_STOCK") == "IN_STOCK"
    )
    availability = "in stock" if in_stock else "out of stock"
    base_price = get_price(product)

    variants = product.get("variants", [])

    if not variants:
        rows.append(_make_row(
            pid, title, description, product_url, main_image, additional_images,
            base_price, availability, gender, age_group, google_cat, "", "", ""
        ))
    else:
        for i, variant in enumerate(variants):
            color = extract_option_value(variant, "color")
            size  = extract_option_value(variant, "size")
            variant_id = f"{pid}_{i}"

            v_price = format_price(
                variant.get("price", {}).get("price", 0),
                variant.get("price", {}).get("currency", "USD")
            ) or base_price

            v_in_stock = variant.get("stock", {}).get("inStock", True)
            v_available = "in stock" if v_in_stock else "out of stock"

            item_group = pid if len(variants) > 1 else ""
            rows.append(_make_row(
                variant_id, title, description, product_url, main_image, additional_images,
                v_price, v_available, gender, age_group, google_cat, color, size, item_group
            ))

    return rows


def _make_row(item_id, title, description, link, image_link, additional_images,
              price, availability, gender, age_group, google_product_category,
              color, size, item_group_id):
    return {
        "id":                      item_id,
        "title":                   title,
        "description":             description,
        "link":                    link,
        "image_link":              image_link,
        "additional_image_link":   ",".join(additional_images[:10]),
        "availability":            availability,
        "price":                   price,
        "brand":                   BRAND,
        "condition":               "new",
        "google_product_category": google_product_category,
        "item_group_id":           item_group_id,
        "color":                   color,
        "size":                    size,
        "gender":                  gender or "",
        "age_group":               age_group or "",
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"[{datetime.utcnow().isoformat()}] Starting Google Merchant Center feed generation...")

    product_collection_pairs = get_all_products_with_collections()

    all_rows = []
    for product, collection_name in product_collection_pairs:
        rows = build_rows(product, collection_name)
        all_rows.extend(rows)

    print(f"  Total feed rows: {len(all_rows)} from {len(product_collection_pairs)} products")

    if not all_rows:
        print("  WARNING: No rows generated.")
        return

    fieldnames = [
        "id", "title", "description", "link", "image_link", "additional_image_link",
        "availability", "price", "brand", "condition", "google_product_category",
        "item_group_id", "color", "size", "gender", "age_group"
    ]

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"  Written to {OUTPUT_FILE}")
    print(f"[{datetime.utcnow().isoformat()}] Done.")


if __name__ == "__main__":
    main()
