#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f .env ]; then
    export $(grep -v '^#' .env | grep -v '^\s*$' | xargs)
fi

# Docker compose command
if [ -f .docker-compose-cmd ]; then
    DOCKER_COMPOSE=$(cat .docker-compose-cmd)
elif docker compose version &> /dev/null; then
    DOCKER_COMPOSE="docker compose"
else
    DOCKER_COMPOSE="docker-compose"
fi

run_sql() {
    $DOCKER_COMPOSE exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -t -A -c "$1"
}

run_sql_pretty() {
    $DOCKER_COMPOSE exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -c "$1"
}

echo "============================================================"
echo "  NDC Pipeline — Incremental Sync Demo"
echo "============================================================"
echo ""

echo "=== Step 1: Run pipeline (ensures data is loaded) ==="
./run_pipeline --skip-labels
echo ""

echo "=== Step 2: Pick a product and show its current state ==="
SAMPLE_NDC=$(run_sql "SELECT product_ndc FROM products WHERE is_current = TRUE AND is_deleted = FALSE AND proprietary_name IS NOT NULL ORDER BY id LIMIT 1;")
SAMPLE_NAME=$(run_sql "SELECT proprietary_name FROM products WHERE product_ndc = '$SAMPLE_NDC' AND is_current = TRUE LIMIT 1;")
echo "  Sample product: $SAMPLE_NAME (NDC: $SAMPLE_NDC)"
echo ""
echo "  Query result (first 15 lines):"
./query --product "$SAMPLE_NDC" | head -15
echo "  ..."
echo ""

echo "=== Step 3: Simulate a data change ==="
echo "  Modifying proprietary_name to 'MODIFIED_FOR_DEMO' and invalidating source_hash..."
run_sql "UPDATE products SET proprietary_name = 'MODIFIED_FOR_DEMO', source_hash = 'forced_mismatch' WHERE product_ndc = '$SAMPLE_NDC' AND is_current = TRUE;" > /dev/null
echo ""
echo "  Current name after modification:"
echo "    $(run_sql "SELECT proprietary_name FROM products WHERE product_ndc = '$SAMPLE_NDC' AND is_current = TRUE;")"
echo ""

# Invalidate the pipeline run hash so Phase 1 re-scans and detects the row-level mismatch
echo "  Invalidating last pipeline run hash to trigger Phase 1 re-scan..."
run_sql "UPDATE pipeline_runs SET source_file_hash = 'invalidated_for_demo' WHERE id = (SELECT id FROM pipeline_runs WHERE status = 'completed' ORDER BY id DESC LIMIT 1);" > /dev/null
echo ""

echo "=== Step 4: Re-run pipeline (detects the hash mismatch, expires old row, inserts corrected row) ==="
./run_pipeline --skip-labels
echo ""

echo "=== Step 5: Verify the product is restored from source ==="
echo "  Current name after re-sync:"
echo "    $(run_sql "SELECT proprietary_name FROM products WHERE product_ndc = '$SAMPLE_NDC' AND is_current = TRUE;")"
echo ""

echo "=== Step 6: Show version history (SCD Type 2) ==="
echo ""
run_sql_pretty "SELECT product_ndc, proprietary_name, is_current, valid_from::timestamp(0), valid_to::timestamp(0) FROM products WHERE product_ndc = '$SAMPLE_NDC' ORDER BY valid_from;"
echo ""
echo "  The expired row (is_current=f) preserves the modified state."
echo "  The new row (is_current=t) has the correct name from the source."
echo ""
echo "============================================================"
echo "  Demo complete."
echo "============================================================"
