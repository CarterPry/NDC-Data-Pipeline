# NDC Data Pipeline — Design Discussion

## 1. Data Sources

### Sources Identified

| Source | Format | Coverage | Update Frequency |
|--------|--------|----------|-----------------|
| FDA NDC Directory (`ndctext.zip`) | Tab-delimited TXT | 100% of marketed NDCs | ~Daily |
| openFDA Drug Label API | JSON REST | ~60-70% of products | Irregular (SPL updates) |
| DailyMed SPL Bulk Data | HL7 XML | ~100% of labels | Daily |
| openFDA Drug/NDC API | JSON REST | ~100% of NDCs | Weekly |

### Sources Used

**Primary — FDA NDC Directory (bulk CSV/TXT):** The `ndctext.zip` file from `accessdata.fda.gov` contains `product.txt` and `package.txt`, providing the authoritative, complete set of every NDC registered with the FDA. This is the "skeleton" of the pipeline — it provides structural data (product names, ingredients, dosage forms, labelers, packages) with 100% coverage and daily freshness.

**Secondary — openFDA Drug Label API:** The `/drug/label.json` endpoint provides consumer-facing label text (indications, warnings, dosage instructions, etc.) in clean JSON format. This "flesh" enrichment layer adds clinically useful text to the structural skeleton.

### Source NOT Used: DailyMed SPL XML

DailyMed's SPL (Structured Product Labeling) XML is the most authoritative source for label text — openFDA itself indexes SPL data from DailyMed. For this submission, I used the openFDA API as a more accessible interface to the same underlying data. Direct DailyMed bulk ingestion would:

- Eliminate API rate-limit dependencies
- Achieve near-100% label coverage (vs. ~60-70% through openFDA)
- Provide access to additional structured data (pharmacokinetics, clinical studies)

The trade-off is significant implementation complexity: SPL is deeply nested HL7-based XML requiring specialized parsing, and the bulk download is 30GB+ uncompressed.

## 2. Schema Design

### Entity Model

```
products (1) ──── (*) packages
    │
    ├──── (*) active_ingredients
    │
    └──── (*) labels
```

**products:** One row per product NDC (the 2-segment identifier). Contains drug identity, labeler, dosage form, route, marketing status.

**packages:** One row per package NDC (the 3-segment identifier). A single product can have many package sizes (e.g., bottles of 30, 60, 100 tablets). References the parent product.

**active_ingredients:** Normalized from the semicolon-delimited `SUBSTANCENAME`, `ACTIVE_NUMERATOR_STRENGTH`, and `ACTIVE_INGRED_UNIT` fields. One row per ingredient per product. This normalization enables queries like "find all products containing acetaminophen" without string parsing.

**labels:** Consumer-facing text from openFDA, stored separately because: (a) labels come from a different source with different update cadence, (b) not every product will match a label, (c) label text is large and would bloat the products table for scan-heavy operations.

### Slowly Changing Dimensions (SCD Type 2)

Every data table uses SCD Type 2 temporal tracking:

- `is_current` — TRUE for the active version, FALSE for historical
- `valid_from` / `valid_to` — when this version was effective
- Partial indexes filter on `is_current = TRUE` so current-data queries remain fast

When a product changes between pipeline runs:
1. The old row is marked `is_current = FALSE, valid_to = NOW()`
2. A new row is inserted with `is_current = TRUE, valid_from = NOW()`

This preserves full version history. You can query "what was this drug's label on date X" with `WHERE valid_from <= X AND (valid_to IS NULL OR valid_to > X)`.

**Trade-off:** More storage over time. In production, historical rows older than a retention threshold (e.g., 2 years) could be archived to a separate table or cold storage.

### Change Detection via Source Hashing

Each record stores a `source_hash` — an MD5 of all source-derived fields. On re-ingestion:

1. Compute hash of incoming record
2. Compare to stored hash of the current DB record
3. If identical → skip (no work needed)
4. If different → expire old version, insert new version

This is far more efficient than comparing every field individually, and it makes the pipeline naturally idempotent: running it twice on the same data produces zero changes.

## 3. NDC Normalization

### The Problem

FDA NDCs use a variable-length format with three segments (labeler-product-package), but the segment widths vary:

| Format | Example | Segment Pattern |
|--------|---------|----------------|
| 4-4-2 | `0573-0150-20` | Short labeler |
| 5-3-2 | `12345-678-90` | Short product code |
| 5-4-1 | `12345-6789-0` | Short package code |

This means the same drug can appear in different databases with different formatting. Joining across sources requires normalization.

### The Solution

Zero-pad each segment to a fixed width: **5-4-2** (11 digits total for package NDCs, 5-4 for product NDCs).

```
4-4-2:  0573-0150-20  →  00573-0150-20
5-3-2:  12345-678-90  →  12345-0678-90
5-4-1:  12345-6789-0  →  12345-6789-00
```

This normalization is implemented in both Python (`src/normalize.py`) and PostgreSQL (`normalize_ndc()` function) to ensure consistency whether normalizing at ingest time or query time.

Both the raw and normalized forms are stored: raw for traceability back to the source, normalized for joining and lookups.

## 4. Data Quality Issues Encountered

### Issues discovered from the FDA source data

**Tab-delimited, not CSV:** The FDA files use tab delimiters despite the `.txt` extension. Using `csv.reader` with `delimiter='\t'` handles this correctly, but it's an easy mistake to make.

**Semicolon sub-delimiters:** `SUBSTANCENAME`, `ACTIVE_NUMERATOR_STRENGTH`, and `ACTIVE_INGRED_UNIT` are parallel arrays joined by semicolons within a single field. These must be split and aligned carefully — the Nth substance corresponds to the Nth strength and Nth unit.

**Date format:** FDA dates are `YYYYMMDD` strings with no separators (e.g., `20240115`). Some fields contain empty strings or non-date values that must be handled gracefully.

**NDC_EXCLUDE_FLAG:** Some rows in the directory are explicitly marked as excluded. These are filtered out during ingestion to avoid polluting the dataset.

**Encoding issues:** Some drug names and labeler names contain non-ASCII characters. Reading with `errors='replace'` prevents crashes at the cost of occasional character substitution.

### Issues discovered during openFDA enrichment

**NDC format mismatch between sources:** The normalized NDC format used for database storage (zero-padded 5-4, e.g., `00573-0150`) does not match what openFDA indexes. openFDA stores the raw, unpadded format (e.g., `0573-0150`). Querying with normalized NDCs produced zero label matches. The fix was to query the API using the raw NDC format stored alongside the normalized form, and to index responses by both raw and normalized forms for reliable matching.

**openFDA query syntax for OR operations:** The openFDA API uses the literal keyword `OR` between terms for disjunctive queries (e.g., `openfda.product_ndc:("0573-0150" OR "0078-0001")`). Early attempts used `+` as a term separator, which openFDA interprets as AND, causing every batch query to return zero results since no single product has multiple NDCs simultaneously.

**openFDA multi-NDC labels:** A single SPL document (and therefore a single openFDA API result) can cover multiple product NDCs from the same labeler. The response's `openfda.product_ndc` array must be iterated to map a single API result back to potentially many products in our database.

**Orphan packages:** Some `package.txt` entries reference product NDCs that do not appear in `product.txt`. These orphan packages cannot be linked to a parent product and are skipped during ingestion. In our dataset, approximately 1–2% of package rows were orphaned.

**Label coverage gap:** Despite batching all ~110k products through the openFDA API, approximately 1.3% of products have no matching label. These tend to be very recently registered products, discontinued legacy drugs, or products from smaller labelers whose SPL submissions are missing from openFDA's index.

## 5. Limitations

- **Label coverage is incomplete.** Products without openFDA matches have NULL label data. In practice, ~98.7% of products matched (107,952 out of ~110k), but the remaining ~1.3% have no label data through this API.
- **Rate-limited enrichment.** First-run label enrichment for 100k+ products is slow due to API rate limits (240 req/min with key, 40 without). The pipeline batches efficiently but still requires patience on initial load.
- **Batch pipeline, not streaming.** Changes between pipeline runs are invisible until the next run. There is no real-time CDC (Change Data Capture).
- **SCD Type 2 storage growth.** Every change creates a new row. Long-running deployments need a retention/archival policy.
- **No drug interaction cross-referencing.** The `drug_interactions` field is free text, not structured data. Programmatic interaction checking would require NLP or a structured source like RxNorm.

## 6. Production Improvements

**Scheduling:** Run the pipeline on a daily cron or Airflow DAG. The source hash check makes daily runs cheap when nothing changes.

**Monitoring & Alerting:** Track pipeline run metrics over time — records changed, label coverage percentage, error rates. Alert on failures or unusual data shifts (e.g., sudden 50% drop in product count suggests a source-side issue, not real removals).

**DailyMed Integration:** For complete label coverage, ingest SPL XML bulk data directly from DailyMed. This eliminates API rate limits and provides access to label sections not indexed by openFDA.

**API Key Management:** Register for an openFDA API key to increase rate limits from 40 to 240 requests per minute.

**Parallel Enrichment:** Use connection pooling and multiple threads for the openFDA enrichment phase. With proper rate limiting per thread, this could cut enrichment time significantly.

**Data Validation Post-Load:** Automated checks after each run: referential integrity (no orphan packages), expected row counts within bounds, label coverage not regressing, full-text search index health.

**Table Partitioning:** For the historical (non-current) rows, partition by `valid_from` date range. This keeps current-data queries fast while allowing efficient archival of old partitions.

**RxNorm Integration:** Link products to RxNorm CUIs for standardized drug concept identification, enabling interoperability with EHR systems and clinical decision support tools.
