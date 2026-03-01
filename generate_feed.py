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
    "Authorization": API_KEY,
    "wix-site-id":   SITE_ID,
    "wix-account-id": ACCOUNT_ID,
    "Content-Type":  "application/json",
}

# ── Collection → gender/age_group mapping ───────────────────────────────────
COLLECTION_GENDER_MAP = {
    "womens clothing": ("female",  "adult"),
    "unisex clothing": ("unisex",  "adult"),
    "lake living":     (None,       None),   # non-apparel, no gender required
}

# ── Load category map ────────────────────────────────────────────────────────
with open("category_map.json") as f:
    CAT_MAP = json.load(f)


# ── Wix API helpers ──────────────────────────────────────────────────────────

def get_all_collections():
    """Return dict of collection_id -> normalized_name."""
    url = "https://www.wixapis.com/stores/v1/collections/query"
    collections = {}
    cursor = None
    while True:
        body = {"query": {"paging": {"limit": 100}}}
        if cursor:
            body["query"]["cursorPaging"] = {"cursor": cursor}
        r = requests.post(url, headers=HEADERS, json=body)
        r.raise_for_status()
        data = r.json()
        for c in data.get("collections", []):
            collections[c["id"]] = c.get("name", "").strip().lower()
        cursor = data.get("metadata", {}).get("cursors", {}).get("next")
        if not cursor:
            break
    return collections


def get_all_products():
    """Return list of all products using cursor-based pagination."""
    url = "https://www.wixapis.com/catalog/v3/products/query"
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


def get_product_collections(product_id):
    """Return list of collection IDs for a product."""
    url = f"https://www.wixapis.com/stores/v1/collections/query"
    body = {
        "query": {
            "filter": json.dumps({"productId": {"$eq": product_id}}),
            "paging": {"limit": 50}
        }
    }
    try:
        r = requests.post(url, headers=HEADERS, json=body)
        r.raise_for_status()
        return [c["id"] for c in r.json().get("collections", [])]
    except Exception:
        return []


def get_collections_for_products(product_ids):
    """
    Use the Wix product-collections endpoint to batch-fetch collection memberships.
    Returns dict of product_id -> [collection_id, ...]
    """
    url = "https://www.wixapis.com/stores/v1/products/collections"
    result = {pid: [] for pid in product_ids}
    # Process in batches of 100
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
    """Return (google_product_category string, google_product_category_id)."""
    title_lower = product_name.lower()

    if collection_name in ("womens clothing", "unisex clothing"):
        # Try to get clothing subcategory from title
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

    # Unknown collection — generic fallback
    return CAT_MAP["clothing_category"], CAT_MAP["clothing_category_id"]


# ── Feed row builder ──────────────────────────────────────────────────────────

def extract_option_value(variant, option_name):
    """Pull a specific option value (e.g. Color, Size) from a variant."""
    for choice in variant.get("choices", []):
        if choice.get("optionName", "").lower() == option_name.lower():
            return choice.get("description", "").strip()
    return ""


def clean_description(html_or_text):
    """Strip HTML tags and normalize whitespace."""
    text = re.sub(r"<[^>]+>", " ", html_or_text or "")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:5000]  # Google limit


def format_price(amount, currency="USD"):
    try:
        return f"{float(amount):.2f} {currency}"
    except (TypeError, ValueError):
        return ""


def build_rows(product, collection_name, all_collections_map):
    """
    Return a list of dicts, one per variant (Google requires variant-level rows).
    """
    rows = []
    pid = product.get("id", "")
    title = product.get("name", "").strip()
    description = clean_description(product.get("description", title))
    slug = product.get("slug", "")
    product_url = f"{STORE_URL}/product-page/{slug}"

    # Images
    main_image = ""
    additional_images = []
    media = product.get("media", {})
    main_media = media.get("main", {})
    if main_media.get("image"):
        main_image = main_media["image"].get("url", "")
    for item in media.get("items", []):
        if item.get("image", {}).get("url") and item["image"]["url"] != main_image:
            additional_images.append(item["image"]["url"])

    # Gender / age_group from collection
    gender, age_group = COLLECTION_GENDER_MAP.get(collection_name, (None, None))

    # Google category
    google_cat, google_cat_id = get_google_category(collection_name, title)

    # Availability
    stock = product.get("stock", {})
    in_stock = stock.get("inStock", True)
    availability = "in stock" if in_stock else "out of stock"

    # Base price (used as fallback if variant has no price)
    base_price = format_price(
        product.get("price", {}).get("price", 0),
        product.get("price", {}).get("currency", "USD")
    )

    variants = product.get("variants", [])

    if not variants:
        # No variants — single row
        row = build_single_row(
            pid, title, description, product_url, main_image, additional_images,
            base_price, availability, gender, age_group, google_cat, "", ""
        )
        rows.append(row)
    else:
        for i, variant in enumerate(variants):
            color = extract_option_value(variant, "color")
            size  = extract_option_value(variant, "size")

            variant_id = f"{pid}_{i}"
            variant_price = format_price(
                variant.get("price", {}).get("price", 0),
                variant.get("price", {}).get("currency", "USD")
            ) or base_price

            variant_available = availability
            if not variant.get("stock", {}).get("inStock", True):
                variant_available = "out of stock"

            row = build_single_row(
                variant_id, title, description, product_url, main_image, additional_images,
                variant_price, variant_available, gender, age_group, google_cat, color, size,
                item_group_id=pid if len(variants) > 1 else ""
            )
            rows.append(row)

    return rows


def build_single_row(item_id, title, description, link, image_link, additional_images,
                     price, availability, gender, age_group, google_product_category,
                     color, size, item_group_id=""):
    row = {
        "id":                       item_id,
        "title":                    title,
        "description":              description,
        "link":                     link,
        "image_link":               image_link,
        "additional_image_link":    ",".join(additional_images[:10]),
        "availability":             availability,
        "price":                    price,
        "brand":                    BRAND,
        "condition":                "new",
        "google_product_category":  google_product_category,
        "item_group_id":            item_group_id,
        "color":                    color,
        "size":                     size,
    }
    # Only add gender/age_group for apparel
    if gender:
        row["gender"]    = gender
        row["age_group"] = age_group
    else:
        row["gender"]    = ""
        row["age_group"] = ""

    return row


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    print(f"[{datetime.utcnow().isoformat()}] Starting Google Merchant Center feed generation...")

    # 1. Fetch all collections
    print("  Fetching collections...")
    all_collections = get_all_collections()
    print(f"  Found {len(all_collections)} collections")

    # Build reverse map: collection_id -> normalized name (only ones we care about)
    target_collection_ids = {}
    for cid, cname in all_collections.items():
        for target in COLLECTION_GENDER_MAP:
            if target in cname:
                target_collection_ids[cid] = target
                break

    print(f"  Matched {len(target_collection_ids)} target collections: {list(target_collection_ids.values())}")

    # 2. Fetch all products
    print("  Fetching products...")
    products = get_all_products()

    # 3. Fetch collection memberships for all products
    print("  Fetching product-collection memberships...")
    product_ids = [p["id"] for p in products]
    product_collection_map = get_collections_for_products(product_ids)

    # 4. Build feed rows
    all_rows = []
    skipped = 0

    for product in products:
        pid = product["id"]
        product_col_ids = product_collection_map.get(pid, [])

        # Find which target collection this product belongs to
        collection_name = None
        for cid in product_col_ids:
            if cid in target_collection_ids:
                collection_name = target_collection_ids[cid]
                break

        if collection_name is None:
            skipped += 1
            continue  # Product not in any target collection — skip

        rows = build_rows(product, collection_name, all_collections)
        all_rows.extend(rows)

    print(f"  Generated {len(all_rows)} feed rows from {len(products) - skipped} products ({skipped} skipped - not in target collections)")

    # 5. Write TSV
    if not all_rows:
        print("  WARNING: No rows generated. Check collection names match exactly.")
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
    print(f"[{datetime.utcnow().isoformat()}] Feed generation complete.")


if __name__ == "__main__":
    main()
