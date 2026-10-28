-- Proposed pricing schema (not applied to the app yet). SQLite syntax; Postgres differs only in AUTOINCREMENT -> GENERATED.

CREATE TABLE vendors (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        VARCHAR(120) NOT NULL UNIQUE,          -- "Chemical.AI", "BioSolveIT", "Simulations Plus" ...
    active      BOOLEAN NOT NULL DEFAULT 1
);

CREATE TABLE products (                                -- as today, with the vendor as a real row instead of free text
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor_id         INTEGER REFERENCES vendors(id),
    name              VARCHAR(120) NOT NULL UNIQUE,
    description       TEXT NOT NULL DEFAULT '',
    line_label        VARCHAR(200),                    -- text printed in the quotation's Product Name column
    segment           VARCHAR(80)  NOT NULL DEFAULT '',
    category          VARCHAR(80)  NOT NULL DEFAULT '',
    deployment        VARCHAR(40)  NOT NULL DEFAULT '', -- Cloud / Local / Local / Cloud
    default_users     VARCHAR(60)  NOT NULL DEFAULT '1',
    price_basis       VARCHAR(20)  NOT NULL DEFAULT 'tiered' CHECK (price_basis IN ('tiered','licence','complimentary')),
    max_users         INTEGER,                         -- highest user count that has a price (NULL for licence / complimentary)
    active            BOOLEAN NOT NULL DEFAULT 1
);

-- One row per product x bundle x number of users. annual_price is the TOTAL for that many users for one year.
-- There is no formula between rows: 2 users is simply whatever you enter for 2 users.
CREATE TABLE product_prices (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id    INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    tier          VARCHAR(20) NOT NULL CHECK (tier IN ('Academic','Commercial')),
    users         INTEGER NOT NULL CHECK (users >= 1),
    annual_price  NUMERIC(14,2) NOT NULL CHECK (annual_price >= 0),
    UNIQUE (product_id, tier, users)
);
CREATE INDEX ix_product_prices_lookup ON product_prices (product_id, tier, users);

-- Quote lines already freeze their own numbers; only the meaning of two columns changes:
--   quote_lines.unit_price = annual price for that user count (copied from product_prices at quote time)
--   quote_lines.amount     = unit_price x years
-- so changing a price later never alters an issued quotation.
