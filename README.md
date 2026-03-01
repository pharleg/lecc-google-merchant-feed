# LECC Google Merchant Center Feed

Automated product feed generator for Lake Erie Clothing Company → Google Merchant Center.

## How it works

1. GitHub Actions runs every 6 hours (or on demand)
2. `generate_feed.py` pulls all products from the Wix Catalog V3 API
3. Products are mapped to Google's required fields based on Wix collection membership
4. Each product variant becomes its own row (required by Google for apparel)
5. Output is committed as `google_feed.tsv` in this repo

## Collection → Google Attribute Mapping

| Wix Collection     | gender | age_group |
|--------------------|--------|-----------|
| Womens Clothing    | female | adult     |
| Unisex Clothing    | unisex | adult     |
| Lake Living        | —      | —         |

## Required GitHub Secrets

| Secret           | Description              |
|------------------|--------------------------|
| `WIX_API_KEY`    | Wix API key (no Bearer)  |
| `WIX_SITE_ID`    | Wix site ID              |
| `WIX_ACCOUNT_ID` | Wix account ID           |

## Google Merchant Center Setup

1. In Google Merchant Center, go to **Products > Feeds > Add feed**
2. Choose **Scheduled fetch**
3. Feed URL: `https://raw.githubusercontent.com/pharleg/lecc-google-merchant-feed/main/google_feed.tsv`
4. Set fetch frequency to **daily** (the file updates every 6 hours)
5. File type: **TSV**

## Google Feed Columns

| Column | Description |
|--------|-------------|
| id | Unique variant ID |
| title | Product name |
| description | Product description |
| link | Product page URL |
| image_link | Main product image |
| additional_image_link | Up to 10 extra images |
| availability | in stock / out of stock |
| price | Price with currency (e.g. 19.99 USD) |
| brand | Lake Erie Clothing Company |
| condition | new |
| google_product_category | Google taxonomy category |
| item_group_id | Groups variants of the same product |
| color | Color variant |
| size | Size variant |
| gender | male / female / unisex |
| age_group | adult |

## Manual Run

Go to **Actions** tab → **Generate Google Merchant Center Feed** → **Run workflow**

## Updating Category Mappings

Edit `category_map.json` to add new product types for Lake Living items or new clothing subcategories. 
No code changes needed — just update the JSON and commit.
