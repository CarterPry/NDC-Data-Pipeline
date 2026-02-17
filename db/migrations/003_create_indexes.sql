-- Products indexes
CREATE INDEX IF NOT EXISTS idx_products_ndc
    ON products (product_ndc) WHERE is_current = TRUE;

CREATE INDEX IF NOT EXISTS idx_products_raw_ndc
    ON products (product_ndc_raw) WHERE is_current = TRUE;

CREATE INDEX IF NOT EXISTS idx_products_name
    ON products USING gin (
        to_tsvector('english', COALESCE(proprietary_name, '') || ' ' || COALESCE(nonproprietary_name, ''))
    ) WHERE is_current = TRUE;

CREATE INDEX IF NOT EXISTS idx_products_labeler
    ON products (labeler_name) WHERE is_current = TRUE;

-- Packages indexes
CREATE INDEX IF NOT EXISTS idx_packages_ndc
    ON packages (package_ndc) WHERE is_current = TRUE;

CREATE INDEX IF NOT EXISTS idx_packages_raw_ndc
    ON packages (package_ndc_raw) WHERE is_current = TRUE;

CREATE INDEX IF NOT EXISTS idx_packages_product
    ON packages (product_id) WHERE is_current = TRUE;

-- Active ingredients indexes
CREATE INDEX IF NOT EXISTS idx_ingredients_product
    ON active_ingredients (product_id) WHERE is_current = TRUE;

CREATE INDEX IF NOT EXISTS idx_ingredients_substance
    ON active_ingredients (substance_name) WHERE is_current = TRUE;

-- Labels indexes
CREATE INDEX IF NOT EXISTS idx_labels_product
    ON labels (product_id) WHERE is_current = TRUE;
