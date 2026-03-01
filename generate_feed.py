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

# ── Hardcoded category IDs (from Wix dashboard URLs) ────────────────────────
CATEGORIES = {
    "b377b9de-cce7-41e2-bb5d-c6267b77ac77": ("womens clothing", "female", "adult"),
    "5e5794a2-f163-4aef-8735-12adcf30f43d": ("unisex clothing", "unisex", "adult"),
    "826bd629-379e-46b0-b579-03d70c511ba0": ("lake living",     None,     None),
}

# ── Load category map ────────────────────────────────────────────────────────
with open("category_map.json") as f:
    CAT_MAP = json.load(f)


# ── Wix API helpers ──────────────────────────────────────────────────────────

def get_all_products():
    """Fetch all products - same approach as working Meta feed."""
    url = "https://www.wixapis.com/stores/v3/products/query"
    products = []
    offset = 0
    while True:
        body = {
            "query": {
                "paging": {"limit": 100, "offset": offset}
            }
        }
        r = requests.post(url, headers=HEADERS, json=body)
        if not r.ok:
            print(f"  Products API error {r.status_code}: {r.text[:300]}")
            r.raise_for_status()
        data = r.json()
        batch = data.get("products", [])
        products.extend(batch)
        if len(batch) < 100:
            break
        offset += 100
    return products


def get_product_detail(product_id):
    """Fetch full product detail including variants."""
    url = f"https://www.wixapis.com/stores/v3/products/{product_id}"
    r = requests.get(url, headers=HEADERS)
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
        lambda p: (p["priceData"]["price"], p["priceData"].get("currency", "USD")),
        lambda p: (p["price"]["price"], p["price"].get("currency", "USD")),
        lambda p: (p["actualPriceRange"]["minValue"]["amount"], p["actualPriceRange"]["minValue"].get("currency", "USD")),
    ]:
        try:
            amount, currency = path(product)
            if amount:
                return format_price(amount, currency)
        except (KeyError, TypeError):
            pass
    return "0.00 USD"


def build_rows(product, collection_name, gender, age_group):
    rows = []
    pid   = product.get("id", "")
    title = product.get("name", "").strip()
    description = clean_description(product.get("description", "") or title)
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

    google_cat = get_google_category(collection_name, title)
    stock = product.get("stock", {})
    in_stock = stock.get("inStock", True)
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

    print("  Fetching all products...")
    products = get_all_products()
    print(f"  Got {len(products)} products")

    # DEBUG: check what get product returns for first product
    if products:
        detail = get_product_detail(products[0]["id"])
        if detail:
            print(f"  Detail keys: {list(detail.keys())}")
            print(f"  mainCategoryId: {detail.get('mainCategoryId')}")
            print(f"  directCategoryIds: {detail.get('directCategoryIds')}")
            print(f"  categories: {detail.get('categories', 'NOT FOUND')}")
        import sys; sys.exit(0)  # stop after debug
        
    if products:
        p = products[0]
        print(f"  Sample product keys: {list(p.keys())}")
        print(f"  Sample product name: {p.get('name')}")

    all_rows = []
    skipped = 0

    for product in products:
        # Check all possible category ID locations in the response
        cat_ids = set()
        for cid in product.get("directCategoryIds", []):
            cat_ids.add(cid)
        for cat in product.get("directCategories", []):
            cat_ids.add(cat.get("id", ""))
        for cat in product.get("categories", []):
            cat_ids.add(cat.get("id", ""))

        collection_name, gender, age_group = None, None, None
        for cid in cat_ids:
            if cid in CATEGORIES:
                collection_name, gender, age_group = CATEGORIES[cid]
                break

        if collection_name is None:
            # Fetch full product detail to get category info
            detail = get_product_detail(product["id"])
            if detail:
                for cid in detail.get("directCategoryIds", []):
                    cat_ids.add(cid)
                for cat in detail.get("directCategories", []):
                    cat_ids.add(cat.get("id", ""))
                # Also grab variants from detail
                product["variants"] = detail.get("variants", [])

            for cid in cat_ids:
                if cid in CATEGORIES:
                    collection_name, gender, age_group = CATEGORIES[cid]
                    break

        if collection_name is None:
            skipped += 1
            print(f"  Skipping '{product.get('name')}' - cat_ids found: {cat_ids}")
            continue

        rows = build_rows(product, collection_name, gender, age_group)
        all_rows.extend(rows)

    print(f"  Total: {len(all_rows)} feed rows ({skipped} products skipped)")

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
