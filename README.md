# NDC Data Pipeline

Ingests the FDA National Drug Code Directory, enriches products with label text from openFDA and DailyMed, and stores everything in PostgreSQL with full version history (SCD Type 2).

**Final coverage: 110,774 / 111,013 products labeled (99.78%)**

## Quick Start

```bash
./setup          # Python venv, Docker Postgres, install deps
./build          # Run database migrations
./run_pipeline   # Download NDC data, ingest, enrich labels
```

## Pipeline Phases

| Phase | Source | What it does |
|-------|--------|-------------|
| 1a | FDA `ndctext.zip` | Ingest products from `product.txt` |
| 1b | FDA `ndctext.zip` | Ingest packages from `package.txt` |
| 1c | — | Soft-delete products/packages removed from source |
| 2a | openFDA `/drug/label.json` | Batch label enrichment by NDC (96.2% coverage) |
| 2b | DailyMed SPL XML | Gap-fill via HL7 XML parsed by LOINC code (+3,980 products) |

### Pipeline Flags

```bash
./run_pipeline --skip-labels      # Skip Phase 2a + 2b
./run_pipeline --skip-dailymed    # Skip Phase 2b only
./run_pipeline --label-limit 500  # Limit enrichment to 500 products
```

## Query CLI

```bash
./query --product "0002-3227"          # Lookup by NDC
./query --search "metformin"           # Full-text search
./query --ingredient "acetaminophen"   # Search by active ingredient
./query --label "0002-3227"            # Show label text for an NDC
```

## Verification

After a full pipeline run, confirm label coverage:

```sql
-- Total coverage (expect 99.78%)
SELECT
    COUNT(*) AS total,
    COUNT(l.id) AS labeled,
    COUNT(*) - COUNT(l.id) AS unlabeled,
    ROUND(100.0 * COUNT(l.id) / COUNT(*), 2) AS pct
FROM products p
LEFT JOIN labels l ON l.product_id = p.id AND l.is_current = TRUE
WHERE p.is_current = TRUE AND p.is_deleted = FALSE;
```

```bash
# Spot-check a labeled product
./query --label "68180-384"   # Tramadol HCl — should show indications, warnings, etc.

# Run unit tests
.venv/bin/pytest tests/
```

## Project Structure

```
setup              # One-command environment setup
build              # Run DB migrations
run_pipeline       # Execute full pipeline
query              # Query CLI
src/
  config.py        # Environment and constants
  db.py            # PostgreSQL connection
  normalize.py     # NDC format normalization (5-4-2)
  pipeline/
    run.py         # Pipeline orchestrator
    download.py    # Download + hash FDA zip
    ingest_products.py
    ingest_packages.py
    enrich_labels.py       # Phase 2a: openFDA
    enrich_dailymed.py     # Phase 2b: DailyMed SPL XML
    cleanup.py     # Soft-delete removed records
  query/
    cli.py         # Query interface
db/migrations/     # SQL schema migrations
tests/             # Unit tests
```

## Design Discussion

See [DISCUSSION.md](DISCUSSION.md) for schema design rationale, SCD Type 2 implementation, NDC normalization details, data quality issues encountered, and production improvement recommendations.
