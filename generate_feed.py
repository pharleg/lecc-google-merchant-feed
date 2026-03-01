#!/usr/bin/env python3
"""
Lake Erie Clothing Company - Google Merchant Center Feed Generator
Supports both Wix Catalog V3 (Categories API) and V1 (Collections API).
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

# ── Collection/Category names → gender/age_group mapping ────────────────────
COLLECTION_GENDER_MAP = {
    "womens clothing": ("female", "adult"),
    "unisex clothing": ("unisex", "adult"),
    "lake living":     (None,      None),
}

# ── Load category map ────────────────────────────────────────────────────────
with open("category_map.json") as f:
    CAT_MAP = json.load(f)


# ── Wix API helpers ──────────────────────────────────────────────────────────

def get_catalog_version():
    """Check if this site uses Catalog V3."""
    url = "https://www.wixapis.com/stores/v3/provision/version"
    try:
        r = requests.get(url, headers=HEADERS)
        if r.ok:
            version = r.json().get("version", "V1")
            print(f"  Catalog version: {version}")
            return version
    except Exception as e:
        print(f"  Could not determine catalog version: {e}")
    return "V1"


def get_categories_v3():
    """Fetch store categories using the V3 Categories API."""
    url = "https://www.wixapis.com/categories/v1/categories"
    categories = {}
    cursor = None
    while True:
        body = {"paging": {"limit": 100}}
        if cursor:
            body["paging"]["cursor"] = cursor
        r = requests.post(url + "/query", headers=HEADERS, json=body)
        print(f"  Categories V3 API status: {r.status_code}")
        if not r.ok:
            print(f"  Categories V3 error: {r.text[:300]}")
            break
        data = r.json()
        for c in data.get("categories", []):
            categories[c["id"]] = c.get("name", "").strip().lower()
        cursor = data.get("pagingMetadata", {}).get("cursors", {}).get("next")
        if not cursor:
            break
    return categories


def get_collections_v1():
    """Fetch store collections using V1 Collections API with site ID in URL."""
    # Try with site ID embedded in path — some Wix endpoints require this
    urls_to_try = [
        f"https://www.wixapis.com/stores/v1/collections/query",
        f"https://www.wixapis.com/stores/v2/collections/query",
    ]
    for url in urls_to_try:
        body = {"query": {}, "includeNumberOfProducts": False}
        print(f"  Trying: POST {url}")
        r = requests.post(url, headers=HEADERS, json=body)
        print(f"  Status: {r.status_code}")
        if r.ok:
            data = r.json()
            collections = {}
            for c in data.get("collections", []):
                collections[c["id"]] = c.get("name", "").strip().lower()
            return collections
        print(f"  Error: {r.text[:200]}")
    return {}


def get_all_categories():
    """Try V3 categories first, fall back to V1 collections."""
    print("  Trying V3 Categories API...")
    cats = get_categories_v3()
    if cats:
        print(f"  Found {len(cats)} categories via V3 API")
        return cats, "v3"

    print("  Trying V1 Collections API...")
    cols = get_collections_v1()
    if cols:
        print(f"  Found {len(cols)} collections via V1 API")
        return cols, "v1"

    print("  WARNING: Could not fetch any categories/collections!")
    return {}, "unknown"


def get_all_products():
    """Fetch all products from V3 products API."""
    url = "https://www.wixapis.com/stores/v3/products/query"
    products = []
    offset = 0
    while True:
        body = {
            "fields": ["ALL_CATEGORIES_INFO"],
            "query": {"paging": {"limit": 100, "offset": offset}}
        }
        r = requests.post(url, headers=HEADERS, json=body)
        r.raise_for_status()
        data = r.json()
        batch = data.get("products", [])
        products.extend(batch)
        if len(batch) < 100:
            break
        offset += 100
    print(f"  Fetched {len(products)} products")
    return products


def get_product_category_ids(product):
    """Extract category/collection IDs from a product across API versions."""
    ids = set()
    # V3 categories
    for cat in product.get("allCategories", product.get("categories", [])):
        cid = cat.get("id") or cat.get("categoryId")
        if cid:
            ids.add(cid)
    # V1 collections embedded in product
    for col in product.get("collections", []):
        cid = col.get("id") or col.get("collectionId")
        if cid:
            ids.add(cid)
    return ids


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
    return re.sub(r"\s+", " ", text).strip()[:5000]


def format_price(amount, currency="USD"):
    try:
        return f"{float(amount):.2f} {currency}"
    except (TypeError, ValueError):
        return ""


def get_price(product):
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

    main_image = ""
    additional_images = []
    media = product.get("media", {})
    if media.get("main", {}).get("image"):
        main_image = media["main"]["image"].get("url", "")
    for item in media.get("items", []):
        img_url = item.get("image", {}).get("url", "")
        if img_url and img_url != main_image:
            additional_images.append(img_url)

    gender, age_group = COLLECTION_GENDER_MAP.get(collection_name, (None, None))
    google_cat = get_google_category(collection_name, title)

    stock = product.get("stock", product.get("inventory", {}))
    in_stock = stock.get("inStock", True) or stock.get("availabilityStatus", "IN_STOCK") == "IN_STOCK"
    availability = "in stock" if in_stock else "out of stock"
    base_price = get_price(product)

    variants = product.get("variants", [])
    if not variants:
        rows.append(_make_row(pid, title, description, product_url, main_image,
                              additional_images, base_price, availability,
                              gender, age_group, google_cat, "", "", ""))
    else:
        for i, variant in enumerate(variants):
            color = extract_option_value(variant, "color")
            size  = extract_option_value(variant, "size")
            variant_id = f"{pid}_{i}"
            v_price = format_price(
                variant.get("price", {}).get("price", 0),
                variant.get("price", {}).get("currency", "USD")
            ) or base_price
            v_available = "in stock" if variant.get("stock", {}).get("inStock", True) else "out of stock"
            item_group = pid if len(variants) > 1 else ""
            rows.append(_make_row(variant_id, title, description, product_url, main_image,
                                  additional_images, v_price, v_available,
                                  gender, age_group, google_cat, color, size, item_group))
    return rows


def _make_row(item_id, title, description, link, image_link, additional_images,
              price, availability, gender, age_group, google_product_category,
              color, size, item_group_id):
    return {
        "id": item_id, "title": title, "description": description,
        "link": link, "image_link": image_link,
        "additional_image_link": ",".join(additional_images[:10]),
        "availability": availability, "price": price, "brand": BRAND,
        "condition": "new", "google_product_category": google_product_category,
        "item_group_id": item_group_id, "color": color, "size": size,
        "gender": gender or "", "age_group": age_group or "",
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"[{datetime.utcnow().isoformat()}] Starting Google Merchant Center feed generation...")

    all_cats, api_version = get_all_categories()
    print(f"  Categories/Collections found ({api_version}):")
    for cid, cname in all_cats.items():
        print(f"    '{cname}' -> {cid}")

    # Map category_id -> target collection name
    target_cat_ids = {}
    for cid, cname in all_cats.items():
        for target in COLLECTION_GENDER_MAP:
            if target in cname:
                target_cat_ids[cid] = target
                break

    print(f"  Matched: {list(set(target_cat_ids.values()))}")

    print("  Fetching products...")
    products = get_all_products()

    # Log first product structure to help debug category field names
    if products:
        p = products[0]
        print(f"  Sample product keys: {list(p.keys())}")
        if "allCategories" in p:
            print(f"  Sample allCategories: {p['allCategories'][:2]}")
        if "categories" in p:
            print(f"  Sample categories: {p['categories'][:2]}")

    all_rows = []
    skipped = 0

    for product in products:
        cat_ids = get_product_category_ids(product)
        collection_name = None
        for cid in cat_ids:
            if cid in target_cat_ids:
                collection_name = target_cat_ids[cid]
                break

        if collection_name is None:
            skipped += 1
            print(f"  Skipping: {product.get('name')} (cat_ids: {cat_ids})")
            continue

        all_rows.extend(build_rows(product, collection_name))

    print(f"  Generated {len(all_rows)} rows ({skipped} products skipped)")

    if not all_rows:
        print("  WARNING: No rows generated.")
        return

    fieldnames = ["id", "title", "description", "link", "image_link", "additional_image_link",
                  "availability", "price", "brand", "condition", "google_product_category",
                  "item_group_id", "color", "size", "gender", "age_group"]

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"  Written to {OUTPUT_FILE}")
    print(f"[{datetime.utcnow().isoformat()}] Done.")


if __name__ == "__main__":
    main()
