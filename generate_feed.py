#!/usr/bin/env python3
"""
Lake Erie Clothing Company - Google Merchant Center Feed Generator
Pulls products from Wix Catalog V3 API and generates a Google-compliant TSV feed.
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

# ── Collection → gender/age_group mapping ───────────────────────────────────
COLLECTION_GENDER_MAP = {
    "womens clothing": ("female", "adult"),
    "unisex clothing": ("unisex", "adult"),
    "lake living":     (None,      None),
}

# ── Load category map ────────────────────────────────────────────────────────
with open("category_map.json") as f:
    CAT_MAP = json.load(f)


# ── Wix API helpers ──────────────────────────────────────────────────────────

def get_all_collections():
    """Return dict of collection_id -> normalized_name using V2 stores API."""
    url = "https://www.wixapis.com/stores/v2/collections/query"
    collections = {}
    offset = 0
    while True:
        body = {"query": {"paging": {"limit": 100, "offset": offset}}}
        r = requests.post(url, headers=HEADERS, json=body)
        r.raise_for_status()
        data = r.json()
        batch = data.get("collections", [])
        for c in batch:
            collections[c["id"]] = c.get("name", "").strip().lower()
        if len(batch) < 100:
            break
        offset += 100
    return collections


def get_all_products():
    """Return list of all products using V3 catalog API with cursor pagination."""
    url = "https://www.wixapis.com/stores/v3/products/query"
    products = []
    cursor = None
    while True:
        body = {"query": {"cursorPaging": {"limit": 100}}}
        if cursor:
            body["query"]["cursorPaging"]["cursor"] = cursor
        r = requests.post(url, headers=HEADERS, json=body)
        r.raise_for_status()
        data = r.json()
        products.extend(data.get("products", []))
        cursor = data.get("metadata", {}).get("cursors", {}).get("next")
        if not cursor:
            break
    print(f"  Fetched {len(products)} products from Wix")
    return products


def get_collections_for_products(product_ids):
    """
    Fetch collection memberships for all products.
    Returns dict of product_id -> [collection_id, ...]
    """
    url = "https://www.wixapis.com/stores/v1/products/collections"
    result = {pid: [] for pid in product_ids}
    for i in range(0, len(product_ids), 100):
        batch = product_ids[i:i+100]
        body = {"productIds": batch}
        try:
            r = requests.post(url, headers=HEADERS, json=body)
            r.raise_for_status()
            for entry in r.json().get("productCollections", []):
                pid = entry.get("productId")
                cid = entry.get("collectionId")
                if pid in result and cid:
                    result[pid].append(cid)
        except Exception as e:
            print(f"  Warning: could not fetch collections for batch: {e}")
    return result


# ── Category helpers ─────────────────────────────────────────────────────────

def get_google_category(collection_name, product_name):
    title_lower = product_name.lower()

    if collection_name in ("womens clothing", "unisex clothing"):
        for kw, subcat in CAT_MAP["clothing_subcategory_keywords"].items():
            if kw in title_lower:
                return subcat, CAT_MAP["clothing_category_id"]
        return CAT_MAP["clothing_category"], CAT_MAP["clothing_category_id"]

    if collection_name == "lake living":
        for kw, info in CAT_MAP["lake_living_keywords"].items():
            if kw in title_lower:
                return info["category"], info["id"]
        default = CAT_MAP["default_lake_living"]
        return default["category"], default["id"]

    return CAT_MAP["clothing_category"], CAT_MAP["clothing_category_id"]


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
        url = item.get("image", {}).get("url", "")
        if url and url != main_image:
            additional_images.append(url)

    gender, age_group = COLLECTION_GENDER_MAP.get(collection_name, (None, None))
    google_cat, google_cat_id = get_google_category(collection_name, title)

    # Availability & base price
    inventory = product.get("inventory", {})
    in_stock = inventory.get("availabilityStatus", "IN_STOCK") == "IN_STOCK"
    availability = "in stock" if in_stock else "out of stock"

    price_range = product.get("actualPriceRange", {})
    base_price = format_price(
        price_range.get("minValue", {}).get("amount", "0.00"),
        price_range.get("minValue", {}).get("currency", "USD")
    )

    variants = product.get("variants", [])

    if not variants:
        row = _make_row(pid, title, description, product_url, main_image,
                        additional_images, base_price, availability,
                        gender, age_group, google_cat, "", "", "")
        rows.append(row)
    else:
        for i, variant in enumerate(variants):
            color = extract_option_value(variant, "color")
            size  = extract_option_value(variant, "size")
            variant_id = f"{pid}_{i}"

            v_price = format_price(
                variant.get("price", {}).get("price", 0),
                variant.get("price", {}).get("currency", "USD")
            ) or base_price

            v_available = "out of stock" if not variant.get("stock", {}).get("inStock", True) else availability

            item_group = pid if len(variants) > 1 else ""
            row = _make_row(variant_id, title, description, product_url, main_image,
                            additional_images, v_price, v_available,
                            gender, age_group, google_cat, color, size, item_group)
            rows.append(row)

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

    print("  Fetching collections...")
    all_collections = get_all_collections()
    print(f"  Found {len(all_collections)} collections")

    # Map collection_id -> target name
    target_collection_ids = {}
    for cid, cname in all_collections.items():
        for target in COLLECTION_GENDER_MAP:
            if target in cname:
                target_collection_ids[cid] = target
                break

    print(f"  Matched target collections: {list(set(target_collection_ids.values()))}")

    print("  Fetching products...")
    products = get_all_products()

    print("  Fetching product-collection memberships...")
    product_ids = [p["id"] for p in products]
    product_collection_map = get_collections_for_products(product_ids)

    all_rows = []
    skipped = 0

    for product in products:
        pid = product["id"]
        product_col_ids = product_collection_map.get(pid, [])

        collection_name = None
        for cid in product_col_ids:
            if cid in target_collection_ids:
                collection_name = target_collection_ids[cid]
                break

        if collection_name is None:
            skipped += 1
            print(f"  Skipping (no target collection): {product.get('name')}")
            continue

        rows = build_rows(product, collection_name)
        all_rows.extend(rows)

    print(f"  Generated {len(all_rows)} rows from {len(products) - skipped} products ({skipped} skipped)")

    if not all_rows:
        print("  WARNING: No rows generated. Check that collection names match exactly.")
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
