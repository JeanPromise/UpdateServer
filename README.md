# Kenya Barcode Hub

A mobile-first Flask website for a Kenyan product-barcode catalogue, developer API, downloadable CSV data, paid account access, M-Pesa transaction-reference review, site posts, and visitor analytics.

## What is included

- Clean, responsive public website with editable brand name, tagline, logo URL, theme colour, home-page copy, announcement, catalogue intro, contact details and payment instructions.
- Catalogue search plus CSV template download.
- Admin catalogue entry, CSV and product-JSON import/upsert and CSV export. Existing barcodes are updated during import rather than duplicated. Imports can also link each product to an `image_url`.
- Full-site SQLite and JSON backups, validated full restore, schema/integrity/foreign-key checks, exclusive restore locking, and an automatic pre-restore safety copy (latest three retained on disk).
- Customer registration/login and account dashboard, password changes, admin-assisted password recovery and API-key replacement.
- Configurable paid API plans, M-Pesa Till/Paybill/account/phone details, transaction-reference submission and admin approval/rejection.
- Access control: approving an order activates its plan for the configured duration; an active plan unlocks the full CSV and API lookups.
- Developer API `GET /api/v1/barcode/{barcode}` with generated API keys, API usage limits, JSON responses, and an optional image URL field for image-enabled subscriptions.
- Admin product-image library with image coverage counters, thumbnails, manual URL linking, local uploads, CSV image linking, batched public-catalogue discovery and downloadable image-file backups.
- Image-inclusive API plans are separate from standard barcode-only plans. Their prices are intentionally left at KSh 0 and unpublished until you set prices in Admin → Plans & pricing.
- API-health dashboard with response-status breakdowns, server failures, missing barcodes, authentication/subscription/limit errors, login failures, and customer-specific troubleshooting history.
- Admin posts/links, including premium-gated resources.
- Visitor page-view analytics with approximate unique browser sessions, masked network addresses, page/referrer/browser details, pagination and CSV export.
- Basic search-engine metadata, canonical URLs, `robots.txt`, `sitemap.xml`, privacy page and a PWA manifest/service worker with an offline shell.
- CSRF protection on browser forms, password hashing, basic admin-login throttling, security headers and upload-size limits.

## Deploy to Render

1. Upload this folder to a GitHub repository.
2. In Render, create a **Blueprint** from the repository (or create a Python Web Service using the supplied `render.yaml`).
3. Add only these two required environment variables in Render:
   - `ADMIN_USERNAME` — the admin name you will use to sign in.
   - `ADMIN_PASSWORD` — a strong, unique password. Use at least 16 characters if possible.
4. Deploy. The site is public; the admin sign-in is at `https://YOUR-SITE.onrender.com/promise212324`.
5. Sign in and open **Website settings** to enter your M-Pesa Till/Paybill/phone, account reference, support contact and branding. Open **Plans & pricing** to set the prices and limits you actually want.

No `SECRET_KEY`, M-Pesa API credential, database URL or other secret environment variable is required for the initial version. The app generates a local session-signing key automatically. The supplied Blueprint uses a paid Starter web service and a 1 GB persistent disk mounted at `/var/data` so SQLite data survives normal restarts and redeploys. Review the cost shown by Render before creating the service. Free web services cannot attach persistent disks.

## Backup, restore and persistent storage

- Admin → **Backup & restore** downloads either a complete SQLite snapshot or a complete row-based JSON backup. Both include catalogue, users, settings, plans, orders/payments, subscriptions, API keys/logs and visitor events.
- Restore validates the uploaded backup before touching live data. SQLite restores require a clean integrity check, matching schema and no broken foreign keys. JSON restores require every table. The site makes a separate safety SQLite copy before replacing the database and keeps the latest three copies on the data disk.
- The current backup schema is version 2. A recognized version-1 JSON or legacy SQLite backup is upgraded during restore so existing users, products, plans, orders and API history are preserved; fields added by the image feature start empty. Uploaded image binaries remain a separate ZIP backup.
- Admin → Product catalogue imports a product CSV/JSON without overwriting site accounts/payments/settings. Images are linked by barcode via the `image_url` field.
- Admin → **Product images** shows total barcode records, products with images, products missing images and items not checked recently. You can search the catalogue, upload JPG/PNG/WEBP/GIF files, add public HTTPS image links, remove wrong links, and review the image gallery. Local images are stored under `product-images/` beside the SQLite database, so the folder must be on persistent storage.
- Use **Download image files ZIP** on the image admin page as a separate backup of locally uploaded image binaries; the database backup contains their URLs, not the binary files. Keep both backups together.
- **Populate images automatically** can keep processing three missing-barcode records every 22 seconds while the admin page remains open. It checks Open Food Facts, Open Beauty Facts and Open Products Facts and accepts only an image returned for the exact barcode. It logs unavailable sources and distinguishes linked images from still-missing items; it never fills rows with guessed URLs or generic placeholder pictures. Public datasets cannot guarantee coverage for every Kenyan/local product. Missing records can be completed by uploading a real photo or importing a trusted `image_url` matched to its barcode. Public catalogue images have source-specific reuse conditions; confirm the license and attribution requirements before offering those image URLs in a paid API. Image-enabled API responses include `image_source` when available.
- Admin → **Users & recovery** can locate an account, view its plan/payment/API history, correct a mistyped name/email, issue a temporary password after verifying identity, replace an API key, or revoke keys. The temporary password and new API key are displayed once and must be shared privately; ask the customer to change the temporary password as soon as they sign in. Existing password hashes and old API-key secrets are not exposed.
- Admin → **API health & errors** shows HTTP statuses (401, 402, 404, 429, 5xx), customer login failures and recent diagnostics. `/health` reports a degraded service when the database check fails instead of always claiming the app is healthy.
- The supplied Blueprint uses a paid Render Starter service with a 1 GB persistent disk at `/var/data`. Review Render pricing before deploying. Keep periodic backup downloads outside Render as an additional recovery layer. No backup method can promise zero failure, so keep at least one off-site copy.

## Add the barcode catalogue

The original seed file includes 938 distinct records supplied in the existing POS/catalogue batches. On the owner's instruction, these are marked verified in the site catalogue. This indicates the owner's accepted POS data status, not independent GS1 certification; original source notes are retained. Nine source rows include prices, and prices are not universally current: source observations should be checked against the relevant retailer/date. The seed runs only once on a fresh database. The uploaded October 22 backup contains a separate catalogue export of 1,567 distinct product records at `data-seed/barcode-hub-catalogue-1567-2026-10-22.csv`. The backup contains no image URLs or binary product photographs, so the new image fill process must retrieve exact-barcode matches from reachable public catalogues or the owner must provide missing images. In Admin → Product catalogue:

- Import `data-seed/verified-pos-products.json` for the original vetted seed batch, or use `data-seed/verified-pos-products.csv`. To load the 1,567 records from the uploaded October 22 backup into a fresh database, import `data-seed/barcode-hub-catalogue-1567-2026-10-22.csv`. Product imports upsert records by barcode and do not replace users, orders, payment history or settings.
- Common columns are supported: `barcode`, `gtin`, `upc`, `ean`, `ean13`, `code`; `name`, `product`, `product_name`; `brand`; `pack_size`, `size`, `amount`, `quantity`; `category`; `price_kes`, `price`; `verified`; and `source`.
- The file `verified-pos-catalogue-only.sqlite3` is for SQLite tools only, not the whole-site Restore page. A short import note and source batches are included in `data-seed/`. Image links are blank in the October 22 CSV until you attach them or run image discovery; no image is fabricated for an unmatched barcode.

Prices may vary by retailer and date. The site does not invent a price when one is missing.

## Payments and access

This initial version uses **manual M-Pesa/reference verification** because you asked to start without payment gateway secrets. Customers pay using the details in Website settings, register their transaction code and details, and see the status in My account. The admin must check the actual M-Pesa statement/record and press **Approve & unlock**. Do not approve based only on a typed reference. The site does not automatically query M-Pesa and does not collect a PIN or OTP.

Approved subscriptions activate for the plan's configured number of days. Plans can be edited in Admin → Plans & pricing. The full catalogue CSV and API endpoint require an active subscription. Product-catalogue JSON is not a full-site backup; use Admin → Backup & restore for full database recovery. A user can create an API key from My account after registration; keys can be revoked there.

## API quick start

Endpoint:

```http
GET /api/v1/barcode/{barcode}
X-API-Key: YOUR_API_KEY
```

Python example:

```python
import requests

response = requests.get(
    "https://YOUR-SITE.onrender.com/api/v1/barcode/ENTER_BARCODE",
    headers={"X-API-Key": "YOUR_API_KEY"},
    timeout=10,
)
print(response.status_code, response.json())
```

Standard plans return barcode/product fields without product images. Image-enabled plans additionally return `image_url` (a public absolute URL or `null`), `image_available` (`true` or `false`), and `image_source` when available; they do not manufacture missing images. If a record is missing, the response is `404 product_not_found`. Common access errors are `401` for a missing/invalid key, `402` for no active subscription and `429` when the plan's lookup limit is reached. Set image-plan prices and enable those plans in Admin → Plans & pricing before offering them publicly.

## Local development (Windows PowerShell)

```powershell
$env:ADMIN_USERNAME="choose-a-name"
$env:ADMIN_PASSWORD="choose-a-long-unique-password"
python -m pip install -r requirements.txt
python app.py
```

Visit `http://127.0.0.1:5000`. For local use, the SQLite database is stored in `instance/barcodehub.sqlite3`.

## Offline/PWA scope

The installable PWA caches the app shell and selected public pages so the site can open and show a useful offline screen. Live search, login, payment submission, API calls and catalogue downloads need an internet connection. It deliberately does not pretend to process offline payments or live product lookups.

## Before public launch

- Configure payment details and support contact.
- Import and check your actual product catalogue.
- Add a persistent disk or managed database before live customer data.
- Read and adapt the privacy page for your business, retention practices and applicable legal obligations.
- Change the initial plan prices and limits if needed.
- Use a strong admin password and never commit credentials or the local `instance/` database to GitHub.
