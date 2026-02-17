-- Pipeline run metadata
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id                  SERIAL PRIMARY KEY,
    started_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at        TIMESTAMPTZ,
    status              TEXT NOT NULL DEFAULT 'running',
    phase               TEXT,
    records_inserted    INTEGER DEFAULT 0,
    records_updated     INTEGER DEFAULT 0,
    records_deleted     INTEGER DEFAULT 0,
    source_file_hash    TEXT,
    notes               TEXT
);

-- Products (SCD Type 2)
CREATE TABLE IF NOT EXISTS products (
    id                          SERIAL PRIMARY KEY,
    product_ndc_raw             TEXT NOT NULL,
    product_ndc                 TEXT NOT NULL,
    product_type                TEXT,
    proprietary_name            TEXT,
    proprietary_name_suffix     TEXT,
    nonproprietary_name         TEXT,
    dosage_form                 TEXT,
    route                       TEXT,
    marketing_category          TEXT,
    application_number          TEXT,
    labeler_name                TEXT,
    pharm_classes               TEXT,
    dea_schedule                TEXT,
    listing_certified_through   DATE,
    start_marketing_date        DATE,
    end_marketing_date          DATE,
    ndc_exclude_flag            BOOLEAN DEFAULT FALSE,

    is_current                  BOOLEAN NOT NULL DEFAULT TRUE,
    valid_from                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_to                    TIMESTAMPTZ,
    source_hash                 TEXT,

    is_deleted                  BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at                  TIMESTAMPTZ,

    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Packages (SCD Type 2)
CREATE TABLE IF NOT EXISTS packages (
    id                      SERIAL PRIMARY KEY,
    product_id              INTEGER NOT NULL REFERENCES products(id),
    package_ndc_raw         TEXT NOT NULL,
    package_ndc             TEXT NOT NULL,
    package_description     TEXT,
    start_marketing_date    DATE,
    end_marketing_date      DATE,
    sample_package          BOOLEAN DEFAULT FALSE,
    ndc_exclude_flag        BOOLEAN DEFAULT FALSE,

    is_current              BOOLEAN NOT NULL DEFAULT TRUE,
    valid_from              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_to                TIMESTAMPTZ,
    source_hash             TEXT,

    is_deleted              BOOLEAN NOT NULL DEFAULT FALSE,
    deleted_at              TIMESTAMPTZ,

    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Active Ingredients (normalized from semicolon-delimited fields)
CREATE TABLE IF NOT EXISTS active_ingredients (
    id                  SERIAL PRIMARY KEY,
    product_id          INTEGER NOT NULL REFERENCES products(id),
    substance_name      TEXT NOT NULL,
    strength            TEXT,
    unit                TEXT,

    is_current          BOOLEAN NOT NULL DEFAULT TRUE,
    valid_from          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_to            TIMESTAMPTZ,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Labels (from openFDA drug/label API)
CREATE TABLE IF NOT EXISTS labels (
    id                          SERIAL PRIMARY KEY,
    product_id                  INTEGER NOT NULL REFERENCES products(id),

    indications_and_usage       TEXT,
    warnings                    TEXT,
    dosage_and_administration   TEXT,
    contraindications           TEXT,
    adverse_reactions           TEXT,
    drug_interactions           TEXT,
    description                 TEXT,
    how_supplied                TEXT,
    storage_and_handling        TEXT,
    purpose                     TEXT,

    openfda_spl_id              TEXT,
    source_last_updated         TEXT,

    is_current                  BOOLEAN NOT NULL DEFAULT TRUE,
    valid_from                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    valid_to                    TIMESTAMPTZ,
    content_hash                TEXT,

    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
