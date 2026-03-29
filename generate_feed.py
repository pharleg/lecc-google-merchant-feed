#!/usr/bin/env python3
"""
Lake Erie Clothing Company - Google Merchant Center Feed Generator
"""

import os
import csv
import json
import requests
import re
from datetime import datetime

# ── Config ──────────────────────────────────────────────────────────────────

API_KEY    = os.environ["WIX_API_KEY"]
SITE_ID    = os.environ["WIX_SITE_ID"]
ACCOUNT_ID = os.environ["WIX_ACCOUNT_ID"]

STORE_URL   = "https://www.lakeerieclothing.com"
BRAND       = "Lake Erie Clothing Company"
OUTPUT_FILE = "google_feed.tsv"

HEADERS = {
    "Authorization":  API_KEY,
    "wix-site-id":    SITE_ID,
    "wix-account-id": ACCOUNT_ID,
    "Content-Type":   "application/json",
}

# ── Hardcoded category IDs (from Wix dashboard URLs) ────────────────────────

CATEGORIES = {
    "b377b9de-cce7-41e2-bb5d-c6267b77ac77": ("womens clothing",  "female", "adult"),
    "5e5794a2-f163-4aef-8735-12adcf30f43d": ("unisex clothing",  "unisex", "adult"),
    "826bd629-379e-46b0-b579-03d70c511ba0": ("lake living",      None,     None),
}

# ── Load category map ────────────────────────────────────────────────────────

with open("category_map.json") as f:
    CAT_MAP = json.load(f)

# ── Wix API helpers ──────────────────────────────────────────────────────────

def get_product_ids_for_category(category_id):
    """Get product IDs belonging to a category via Categories API."""
    url = f"https://www.wixapis.com/categories/v1/categories/{category_id}/list-items"
    product_ids = []
    cursor = None

    while True:
        body = {
            "treeReference": {"appNamespace": "@wix/stores"},
            "paging": {"limit": 100}
        }
        if cursor:
            body["paging"]["cursor"] = cursor

        r = requests.post(url, headers=HEADERS, json=body)
        if not r.ok:
            print(f"  Category items API error {r.status_code}: {r.text[:300]}")
            r.raise_for_status()

        data  = r.json()
        items = data.get("items", [])
        for item in items:
            pid = item.get("catalogItemId") or item.get("itemId") or item.get("id")
            if pid:
                product_ids.append(pid)

        next_cursor = data.get("pagingMetadata", {}).get("cursors", {}).get("next")
        if not next_cursor or len(items) < 100:
            break
        cursor = next_cursor

    return product_ids


def get_products_by_ids(product_ids):
    """Fetch products by ID list using V3 query."""
    url      = "https://www.wixapis.com/stores/v3/products/query"
    products = []

    for i in range(0, len(product_ids), 100):
        batch = product_ids[i:i + 100]
        body  = {
            "query": {
                "filter": {"id": {"$in": batch}},
                "paging": {"limit": 100}
            }
        }
        r = requests.post(url, headers=HEADERS, json=body)
        if not r.ok:
            print(f"  Products API error {r.status_code}: {r.text[:300]}")
            r.raise_for_status()
        products.extend(r.json().get("products", []))

    return products


def get_product_detail(product_id):
    """Fetch full product detail including variantsInfo."""
    url = f"https://www.wixapis.com/stores/v3/products/{product_id}"
    r   = requests.get(url, headers=HEADERS)
    if not r.ok:
        print(f"  Warning: could not fetch detail for {product_id}: {r.status_code}")
        return None
    return r.json().get("product", {})

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

def get_choice_name(options, option_id, choice_id):
    """Resolve option/choice IDs to human-readable names."""
    for option in options:
        if option.get("id") == option_id:
            for choice in option.get("choicesSettings", {}).get("choices", []):
                if choice.get("choiceId") == choice_id:
                    return choice.get("name", "").strip()
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
        lambda p: (p["actualPriceRange"]["minValue"]["amount"], "USD"),
        lambda p: (p["priceData"]["price"],  p["priceData"].get("currency", "USD")),
        lambda p: (p["price"]["price"],       p["price"].get("currency", "USD")),
    ]:
        try:
            amount, currency = path(product)
            if amount:
                return format_price(amount, currency)
        except (KeyError, TypeError):
            pass
    return "0.00 USD"


def build_rows(product, detail, collection_name, gender, age_group):
    rows = []
    pid  = product.get("id", "")

    title       = product.get("name", "").strip()
    description = clean_description(product.get("description", "") or title)
    slug        = product.get("slug", "")
    product_url = f"{STORE_URL}/product-page/{slug}"

    main_image        = ""
    additional_images = []
    media = product.get("media", {})
    if media.get("main", {}).get("image"):
        main_image = media["main"]["image"].get("url", "")
    for item in media.get("items", []):
        img_url = item.get("image", {}).get("url", "")
        if img_url and img_url != main_image:
            additional_images.append(img_url)

    google_cat = get_google_category(collection_name, title)
    base_price = get_price(product)

    # Always in stock — made-to-order / print-on-demand model
    availability = "in stock"

    options  = detail.get("options", []) if detail else []
    variants = []
    if detail:
        variants = detail.get("variantsInfo", {}).get("variants", [])

    if not variants:
        rows.append(_make_row(
            pid, title, description, product_url, main_image,
            additional_images, base_price, availability,
            gender, age_group, google_cat, "", "", ""
        ))
    else:
        for variant in variants:
            color, size = "", ""

            for choice_ref in variant.get("choices", []):
                ids      = choice_ref.get("optionChoiceIds", {})
                opt_id   = ids.get("optionId", "")
                choice_id = ids.get("choiceId", "")
                name     = get_choice_name(options, opt_id, choice_id)
                for option in options:
                    if option.get("id") == opt_id:
                        opt_name = option.get("name", "").lower()
                        if "color" in opt_name:
                            color = name
                        elif "size" in opt_name:
                            size = name

            # Use stable Wix variant ID — not loop index
            variant_id = variant.get("id") or variant.get("variantId") or f"{pid}_{color}_{size}".strip("_")

            v_price = format_price(
                variant.get("price", {}).get("actualPrice", {}).get("amount", 0)
            ) or base_price

            item_group = pid if len(variants) > 1 else ""
            v_image    = variant.get("media", {}).get("image", {}).get("url", "") or main_image

            rows.append(_make_row(
                variant_id, title, description, product_url, v_image,
                additional_images, v_price, availability,
                gender, age_group, google_cat, color, size, item_group
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

    all_rows = []
    seen_ids = set()

    for cat_id, (collection_name, gender, age_group) in CATEGORIES.items():
        print(f"  Fetching products for '{collection_name}'...")

        try:
            product_ids = get_product_ids_for_category(cat_id)
        except Exception as e:
            print(f"  ERROR fetching category items: {e}")
            continue

        print(f"  Got {len(product_ids)} product IDs")
        if not product_ids:
            continue

        products = get_products_by_ids(product_ids)
        print(f"  Fetched {len(products)} products")

        for product in products:
            pid = product["id"]
            if pid in seen_ids:
                continue
            seen_ids.add(pid)

            detail = get_product_detail(pid)
            rows   = build_rows(product, detail, collection_name, gender, age_group)
            all_rows.extend(rows)

    print(f"  Total: {len(all_rows)} feed rows from {len(seen_ids)} products")

    if not all_rows:
        print("  WARNING: No rows generated.")
        return

    fieldnames = [
        "id", "title", "description", "link", "image_link", "additional_image_link",
        "availability", "price", "brand", "condition", "google_product_category",
        "item_group_id", "color", "size", "gender", "age_group",
    ]

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"  Written to {OUTPUT_FILE}")
    print(f"[{datetime.utcnow().isoformat()}] Done.")


if __name__ == "__main__":
    main()
