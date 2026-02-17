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

**Secondary — openFDA Drug Label API (Phase 2a):** The `/drug/label.json` endpoint provides consumer-facing label text (indications, warnings, dosage instructions, etc.) in clean JSON format. Batch queries by NDC with fan-out matching (one SPL covers many product NDCs) achieved 96.2% product coverage.

**Tertiary — DailyMed SPL XML (Phase 2b):** For the ~4,200 products openFDA missed, the pipeline queries DailyMed's REST API (`/v2/spls.json?ndc=`) to find each product's SPL set ID, then fetches the full HL7 SPL XML and extracts label sections by LOINC code. This gap-fill phase enriched 3,980 additional products (94.3% of the gap), bringing total label coverage to **99.78%** (110,774 / 111,013).

The DailyMed endpoint only serves SPL content as XML (the JSON detail endpoint returns HTTP 415), so the pipeline parses HL7 XML using LOINC section codes mapped to the same label fields used by openFDA enrichment:

| LOINC Code | Label Field |
|------------|-------------|
| 34067-9 | indications_and_usage |
| 43685-7, 34071-1 | warnings |
| 34068-7 | dosage_and_administration |
| 34070-3 | contraindications |
| 34084-4 | adverse_reactions |
| 34073-7 | drug_interactions |
| 34089-3 | description |
| 34069-5 | how_supplied |
| 44425-7 | storage_and_handling |
| 69718-5, 59845-8 | purpose |

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

**Label coverage gap (openFDA alone):** Despite batching all ~111k products through the openFDA API, approximately 3.8% (~4,200 products) had no matching label. Phase 2b (DailyMed) closed most of this gap, but 239 products remain unlabeled across both sources.

### Remaining 239 unlabeled products

After two-phase enrichment (openFDA + DailyMed), 239 products (0.22%) have NULL label fields. Manual verification against the DailyMed website and openFDA API confirmed these are genuinely absent — "No Drug Package Labels found" for every product checked. The breakdown:

| Category | Count | Reason |
|----------|-------|--------|
| Unapproved homeopathics | 109 | No SPL required; marketed under CPG 400.400 |
| OTC monograph drugs | 89 | Marketed under monograph; no labeling SPL filed |
| Misc (ANDAs, BLAs, shortage, recently registered) | 41 | Small international labelers, repackagers, niche products |

These NULL label fields accurately reflect the FDA's own data model: these manufacturers submitted NDC listing SPLs (which register the product) but never filed labeling SPLs (which contain the electronic label text). No electronic label text exists in any federal system for these products.

## 5. Limitations

- **239 products with no label text (0.22%).** These genuinely have no electronic label in openFDA or DailyMed. Primarily unapproved homeopathics and OTC monograph products.
- **Rate-limited enrichment.** First-run enrichment (openFDA + DailyMed) for 100k+ products is slow due to API rate limits. The pipeline batches efficiently but initial load requires patience.
- **Batch pipeline, not streaming.** Changes between pipeline runs are invisible until the next run. There is no real-time CDC (Change Data Capture).
- **SCD Type 2 storage growth.** Every change creates a new row. Long-running deployments need a retention/archival policy.
- **No drug interaction cross-referencing.** The `drug_interactions` field is free text, not structured data. Programmatic interaction checking would require NLP or a structured source like RxNorm.

## 6. Production Improvements

**Scheduling:** Run the pipeline on a daily cron or Airflow DAG. The source hash check makes daily runs cheap when nothing changes.

**Monitoring & Alerting:** Track pipeline run metrics over time — records changed, label coverage percentage, error rates. Alert on failures or unusual data shifts (e.g., sudden 50% drop in product count suggests a source-side issue, not real removals).

**DailyMed Bulk Ingestion:** The current DailyMed integration queries per-product via REST API. For faster runs, ingest the SPL bulk download directly (~30GB uncompressed) to eliminate per-product API calls and access additional structured sections (pharmacokinetics, clinical studies).

**API Key Management:** Register for an openFDA API key to increase rate limits from 40 to 240 requests per minute.

**Parallel Enrichment:** Use connection pooling and multiple threads for the enrichment phases. With proper rate limiting per thread, this could cut enrichment time significantly.

**Data Validation Post-Load:** Automated checks after each run: referential integrity (no orphan packages), expected row counts within bounds, label coverage not regressing, full-text search index health.

**Table Partitioning:** For the historical (non-current) rows, partition by `valid_from` date range. This keeps current-data queries fast while allowing efficient archival of old partitions.

**Label Status Classifier:** For the 239 unlabeled products, a `label_status` column could replace opaque NULLs with actionable metadata (e.g., `homeopathic_exempt`, `monograph_no_spl`, `pending_submission`), derived from the product's `marketing_category` and `start_marketing_date`.

**RxNorm Integration:** Link products to RxNorm CUIs for standardized drug concept identification, enabling interoperability with EHR systems and clinical decision support tools.
