# Quotation System

Web app for Molecular Solutions that turns a few inputs (customer, products, users/years, discount, GST)
into a quotation **Word + PDF that looks exactly like `templates/master_quotation.docx`**
(built from `Quotation_Template.docx`, with the top section redone to match the Word-designed reference).

* FastAPI + SQLAlchemy + SQLite (WAL) · Alpine.js UI (no build step) · LibreOffice for PDF
* Sign-in is done by **Cloudflare Access** (no passwords in the app); an administrator lists who may use it and their role; an **Admin** panel lists every quotation
* Data (products, prices, customers, packages) lives in SQL and is managed on the **Data** page:
  upload `.csv` / `.xlsx`, or edit the table directly

## How a quotation is made

1. **Quotes → New quotation** — pick/type the customer, add products (prices come from the database for the
   chosen Academic/Commercial tier), set users and years per row, set discount and GST.
2. The summary on the right is calculated by the server (`app/services/pricing.py`); the browser never does money maths.
3. **Save** assigns the next global number (`MS-AB-175`, …). **⬇ Word / ⬇ PDF** save and download.
   **Preview** renders an unsaved quote without using a number.
4. Saved quotes are frozen: later price or name changes never alter them. **Revise** makes `…-R1`, **Copy** a new number.

Pricing: the **price for the number of users and the length of the quote**, per row. Each product has its own fixed price for each number of users (1–25), separately for Academic and Commercial, kept on the **Prices** page; 2 users is not 2 × the 1-user price. Each also has an optional **3-year price** (the total for 3 years, not 3 × annual). A 1-year quote uses the annual price and a 3-year quote uses the 3-year price (the line's *Price / yr* then shows a third of it, and the amount is the 3-year price exactly). **Any other length, a 3-year quote with no 3-year price, more than 25 users, or a count without a price has no catalogue price: type the price or the total on the quotation.** The builder also lets you type the row's **Total amount**; the price is then worked out from it (total ÷ years) and the total stays exactly as typed. Text users such as *Floating* or *1 Group License* print as written and count as 1. A *Free* row prints “Complimentary Renewed Annually” and adds ₹0. Then
`List total → Discount (own row, no percentage) → one price row`. The price row always reads **TOTAL AMOUNT INC. GST** (GST % from the quote, default 18, added in). No separate GST rows are printed, and there is no "+ GST" choice.

## Run locally (development)

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
# PDF needs LibreOffice and the Calibri-compatible Carlito font (fonts/ has it):
mkdir -p ~/.fonts && cp fonts/*.ttf ~/.fonts && fc-cache -f
python scripts/seed.py                       # first time: loads seed/products.csv
QUOTE_ADMIN_EMAIL=you@example.com QUOTE_CF_TEAM_DOMAIN=your-team.cloudflareaccess.com QUOTE_CF_AUD=your-aud-tag uvicorn app.main:app --reload
# every request needs the signed token Cloudflare Access adds, so a browser on localhost is refused (401);
# use the tests (they sign their own tokens), or reach the app through the tunnel hostname
pytest                                       # needs soffice + pdftoppm for the layout test
```

## Run in Docker (local test)

```bash
cp .env.example .env     # set QUOTE_ADMIN_EMAIL, QUOTE_CF_TEAM_DOMAIN and QUOTE_CF_AUD
docker compose up -d --build
# reached through the Cloudflare Tunnel; 127.0.0.1:8100 answers 401 to a browser (QUOTE_PORT in .env changes the port)
```

The app listens on `127.0.0.1` only. Data is in the `quote-data` volume; a `backup`
service writes a daily database copy to `/data/backups` (14 kept) — also copy that folder off the server. The first
start applies migrations and loads `seed/products.csv` if there are no products. `docker compose down` keeps the data;
`docker compose down -v` deletes it.

## Putting it behind Cloudflare Access

A `cloudflared` container on `molecular-discovery-network` forwards `https://<hostname>` to `http://quotation-app:8000`
(Zero Trust → Networks → Tunnels → Published application routes; service type **HTTP**). A self-hosted Access application
on the same hostname lets Cloudflare ask people to sign in (One-time PIN by email) before anything reaches the app, and
an **Allow** policy lists who may do that.

Cloudflare then adds a signed token (`Cf-Access-Jwt-Assertion`) to every request. The app checks its signature against
`https://<team>.cloudflareaccess.com/cdn-cgi/access/certs` and its audience against the application's **AUD tag**, takes the
verified email, and looks it up in the **users** table: no row, or a deactivated one, gets "403 not set up"; otherwise the
row's role (admin or user) applies. The plain email header is never trusted, so something that reaches the container
without going through Cloudflare (another container, `127.0.0.1:8100`) gets 401.

Settings (environment): `QUOTE_CF_TEAM_DOMAIN` and `QUOTE_CF_AUD` (both required; the app will not start without them),
`QUOTE_ADMIN_EMAIL`, `QUOTE_ADMIN_NAME` (first administrator only), `QUOTE_DATA_DIR`, `DATABASE_URL` (defaults to SQLite in
the data dir; a Postgres URL also works — back up with `pg_dump`).

## Users and the Admin panel

* On first start the app creates one administrator from `QUOTE_ADMIN_EMAIL` (existing quotations are assigned to them).
  It must be the email that person signs in with at Cloudflare. If the database already has users, that variable is ignored.
* **Admin → Users**: add a person (name, email, role). There are no passwords. They must also be in the Cloudflare Access
  Allow policy to reach the app at all. **Deactivate** refuses them on their very next request but keeps their quotations;
  a role change also applies at once. You cannot deactivate yourself or the last administrator.
* **Admin → All quotations**: every quotation with who created it; filter by user, status or text; open, download Word/PDF.
* Users see and edit only their own quotations (a revision stays with the quote's owner; a copy belongs to whoever made it).
  Administrators can open any quotation. **Data** and **Settings** (prices, products, numbering) are administrator-only.
* **Log out** goes to `/cdn-cgi/access/logout`, which ends the Cloudflare session. How long people stay signed in is the
  Access application's session duration.

## Data page

| Tab | Upload columns (header names are matched case-insensitively) |
|---|---|
| Products | `Product Name`* (unique; the key), Category, Vendor, Segment, Unit, Description, Quote Label, Deployment, Default Users, Price Basis, Catalog Blocks, Active (the old `Academic Price` / `Commercial Price` columns are ignored; see **Prices** below) |
| Customers | `Name`*, Department, Institution, Address, Email, Phone, Type (Academic/Commercial) |
| Packages | `Package`*, `Product Name`*, Users, Years (one row per product in the package) |

* The original 9-column file still uploads: its `Product ID` column is ignored (products are matched by name). Only INR is supported; another currency is rejected per row.
* An upload first shows a **preview with row-by-row problems**; only the valid rows are imported. Existing rows are updated by product name (blank cells keep the stored value); nothing is deleted. “✕” on a product hides it (old quotes keep working).
* **Quote Label** is the text printed in the PRODUCT NAME column (falls back to Product Name). **Catalog Blocks** lists the
  “Our Software Offering” sections that turn red when the product is quoted (keys are on the *Catalog blocks* tab).
* Price Basis: `per_user_year`, `per_license_year`, `complimentary`.

## Prices page (admin)

A product, a bundle (Academic / Commercial) and a number of users have one price: the **total for that many users for one year**,
and optionally a **3-year price** (table `product_prices`: `product_id, tier, users, annual_price, three_year_price`, unique per product + tier + users). A *licence* product (Price Basis
`per_license_year`: Floating, Group, …) has a single price, stored at 1 user; a complimentary product has none.

* Pick a product on the left (⚠ = no prices yet, 12/25 = partly priced), then the Academic or Commercial tab, and type the price for each number of users — or
  select a cell and **paste a column from Excel**. The page warns about a price that doesn't rise with users or a missing count in between.
* **Upload prices**: an Excel file with sheets `Academic` and `Commercial` (a row per product, a column per number of users 1–25; blank cells change nothing),
  or a CSV/Excel with columns `Product Name, Tier, Users, Annual Price[, 3 Year Price]`. 3-year prices in the Excel shape go on sheets `Academic 3 Years` and `Commercial 3 Years` (a blank cell changes nothing). A preview lists problems row by row first. **⬇ Excel / CSV** exports the same shapes.
* `seed/dummy_user_prices.csv` and `.xlsx` hold **placeholder** prices (made by `scripts/dummy_prices.py`); replace them with real ones.
* **Real prices from the company's pricing chart** (`PRICING CHART - UPDATED 2026.xlsx`, one sheet per vendor): `scripts/import_pricing.py` reads the
  *Quote Price (1 year)* and, where a table has one, *Quote Price (3 years)* column of every table, replaces the price lists of the products the chart covers, blanks the prices of products it does not mention, and adds the products the database lacks
  (cores / Xunits / GPU / credits options and "Unlimited" rows become one licence product each). It is a **dry run** unless you pass `--apply`
  (SQLite is backed up first), prints counts only (no prices), and stops if a table is not where `TABLES` in the script expects it.
  `python scripts/import_pricing.py "PRICING CHART - UPDATED 2026.xlsx"`, then again with `--apply`. Which table feeds which product is the `TABLES` list at the top of the script.

## Descriptions, discount and Terms & Conditions

* **Description** (Data → Products): printed under the product name in the PRODUCT NAME column, in smaller type. It is set only in the Data page; each quotation keeps its own copy, so later edits do not change issued quotes.
* The **Discount** row of the quotation shows the amount only; the percentage is used for the calculation and shown in the builder, not in the document.
* **Settings → Terms & Conditions** (drop-down): edit, add, remove and reorder the points on page 2. Formatting: `**bold**`, `__italic__`, `==highlight==`;
  placeholders `{validity_date}`, `{payment_terms}`, `{license_term}`; e-mail addresses turn blue. A quotation keeps the terms it was created with.
  Until you change them, the template's own text is used unchanged.
* **Data page**: edit cells, then **Save changes** (top right; Enter saves the row; the row's own Save sits in a pinned right-hand column).

## E-mailing a quotation

The **✉ Email** button (quote builder and Quotes list) saves the quote and sends its PDF to the recipient list an
administrator keeps in **Settings → E-mail recipients**. Nothing is sent automatically; each click asks for confirmation
and shows who will get it. A quote shows "Last e-mailed …" once sent.

Outgoing mail needs an SMTP account, set in `.env` (never stored in the database), then `docker compose up -d`:

```
QUOTE_SMTP_HOST=smtp.gmail.com      # Gmail/Workspace: use an App Password, not the account password
QUOTE_SMTP_PORT=587
QUOTE_SMTP_SECURITY=starttls        # starttls | ssl (port 465) | none
QUOTE_SMTP_USER=sales@your-company.com
QUOTE_SMTP_PASSWORD=...
QUOTE_SMTP_FROM=sales@your-company.com
```

Until `QUOTE_SMTP_HOST` is set the button explains that mail isn't set up. Settings has a "Send me a test e-mail" button.

## Changing the document

`templates/master_quotation.docx` is generated by `python scripts/build_master.py` from `reference/Quotation_Template.docx` (it rebuilds the header
— banner, title and logo, the TO block and the details box (quote number, date, licence type and duration) like the purchase order, the three-column company footer on every page, navy table header — and swaps in `templates/logo_source.png`); re-run it after
changing the logo or header spacing. The layout, company address, logo, standard terms and the catalog descriptions are in `templates/master_quotation.docx`.
The app only fills the quote number, date, customer block, licence type, pricing table, validity/payment/licence-term
lines and the red/black colouring of the catalog. To edit fixed text, edit the Word file — but keep its structure
(the filler checks anchor text and fails loudly if the template layout changed; see `ANCHORS`/`CATALOG_BLOCKS` in
`app/services/docgen.py`). `tests/test_fidelity.py` renders the master and the app's output for the master's own
sample and requires them to be pixel-identical apart from the price cells, and checks the header layout (TO block left, details box right, company details only in the footer).

## Notes and limits

* PDF is made by LibreOffice from the same `.docx`; the Docker image installs Carlito so it matches. For PDFs on a machine without LibreOffice the app offers the Word file and says why.
* “Prepared by” defaults to the creator's name; there is no per-edit history yet, only who created each quotation.
* Academic quote numbers come from the shared sequence (`MS-AB-175`, format in Settings). For a Commercial quote the user types the number on the quote itself (`MS-` is fixed, e.g. `MS-CA-26-058`); it must be unique and does not use the sequence.
