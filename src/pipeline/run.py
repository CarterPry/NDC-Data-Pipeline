"""Pipeline orchestrator: coordinates all phases of the NDC data pipeline."""

import argparse
import os
import shutil
import time

from src import config
from src.db import connection
from src.pipeline.download import download_ndc_zip, check_source_unchanged
from src.pipeline.ingest_products import ingest_products
from src.pipeline.ingest_packages import ingest_packages
from src.pipeline.enrich_labels import enrich_labels
from src.pipeline.enrich_dailymed import enrich_from_dailymed
from src.pipeline.cleanup import soft_delete_missing


def create_run_record(conn, source_hash: str = None) -> int:
    """Insert a new pipeline_runs row and return its id."""
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO pipeline_runs (status, source_file_hash)
        VALUES ('running', %s)
        RETURNING id
    """, (source_hash,))
    run_id = cur.fetchone()[0]
    conn.commit()
    cur.close()
    return run_id


def update_run_record(conn, run_id: int, status: str, phase: str = None,
                      inserted: int = 0, updated: int = 0, deleted: int = 0,
                      notes: str = None):
    cur = conn.cursor()
    cur.execute("""
        UPDATE pipeline_runs
        SET status = %s, phase = %s, completed_at = NOW(),
            records_inserted = %s, records_updated = %s, records_deleted = %s,
            notes = %s
        WHERE id = %s
    """, (status, phase, inserted, updated, deleted, notes, run_id))
    conn.commit()
    cur.close()


def run_pipeline(skip_labels: bool = False, skip_dailymed: bool = False,
                 label_limit: int = 0, source_dir: str = None):
    """Execute the full NDC data pipeline."""
    start_time = time.time()
    print("=" * 60)
    print("NDC Data Pipeline")
    print("=" * 60)

    total_inserted = 0
    total_updated = 0
    total_deleted = 0

    with connection() as conn:
        # Phase 0: Download and check for changes
        skeleton_unchanged = False

        if source_dir:
            extract_dir = source_dir
            file_hash = "local-override"
            print(f"\nPhase 0: Using local source directory: {source_dir}")
        else:
            print("\nPhase 0: Downloading NDC directory...")
            tmp_dir = config.TMP_DIR
            extract_dir, file_hash = download_ndc_zip(tmp_dir)

            cur = conn.cursor()
            if check_source_unchanged(file_hash, cur):
                skeleton_unchanged = True
                print("  Source data unchanged since last successful run — skipping Phase 1.")
            cur.close()

        run_id = create_run_record(conn, file_hash)

        try:
            # Phase 1: Skeleton ingestion (skip if source unchanged)
            if skeleton_unchanged:
                print("\nPhase 1: Skipped (source unchanged)")
                product_stats = {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0, "seen_raw_ndcs": set()}
                package_stats = {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0, "seen_raw_ndcs": set()}
                cleanup_stats = {"products_deleted": 0, "packages_deleted": 0}
            else:
                print("\nPhase 1a: Ingesting products...")
                product_stats = ingest_products(extract_dir, conn)

                print("\nPhase 1b: Ingesting packages...")
                package_stats = ingest_packages(extract_dir, conn)

                total_inserted = product_stats["inserted"] + package_stats["inserted"]
                total_updated = product_stats["updated"] + package_stats["updated"]

                print("\nPhase 1c: Detecting removed records...")
                cleanup_stats = soft_delete_missing(
                    conn,
                    product_stats.get("seen_raw_ndcs", set()),
                    package_stats.get("seen_raw_ndcs", set()),
                )
                total_deleted = (cleanup_stats["products_deleted"] +
                                 cleanup_stats["packages_deleted"])

            # Phase 2: Label enrichment (always runs unless explicitly skipped)
            if not skip_labels:
                print("\nPhase 2: Enriching labels from openFDA...")
                label_stats = enrich_labels(conn, limit=label_limit)
                total_inserted += label_stats["enriched"]
            else:
                print("\nPhase 2: Skipped (--skip-labels)")

            # Phase 2b: DailyMed gap-fill for products openFDA missed
            if not skip_labels and not skip_dailymed:
                print("\nPhase 2b: Filling label gaps from DailyMed SPL...")
                dm_stats = enrich_from_dailymed(conn, limit=label_limit)
                total_inserted += dm_stats["enriched"]
            elif skip_dailymed:
                print("\nPhase 2b: Skipped (--skip-dailymed)")

            # Record success
            elapsed = time.time() - start_time
            update_run_record(
                conn, run_id, "completed",
                phase="all",
                inserted=total_inserted,
                updated=total_updated,
                deleted=total_deleted,
                notes=f"Completed in {elapsed:.1f}s",
            )

            print("\n" + "=" * 60)
            print(f"Pipeline complete ({elapsed:.1f}s)")
            print(f"  Inserted: {total_inserted}")
            print(f"  Updated:  {total_updated}")
            print(f"  Deleted:  {total_deleted}")
            print("=" * 60)

        except Exception as e:
            update_run_record(conn, run_id, "failed", notes=str(e))
            print(f"\nPipeline FAILED: {e}")
            raise

        finally:
            # Clean up temp directory (unless using local source)
            if not source_dir and os.path.exists(config.TMP_DIR):
                shutil.rmtree(config.TMP_DIR, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="NDC Data Pipeline")
    parser.add_argument("--skip-labels", action="store_true",
                        help="Skip openFDA label enrichment (Phase 2)")
    parser.add_argument("--skip-dailymed", action="store_true",
                        help="Skip DailyMed SPL gap-fill (Phase 2b)")
    parser.add_argument("--label-limit", type=int, default=0,
                        help="Max products to enrich with labels (0=all)")
    parser.add_argument("--source-dir",
                        help="Use a local directory instead of downloading (for testing)")
    args = parser.parse_args()

    run_pipeline(
        skip_labels=args.skip_labels,
        skip_dailymed=args.skip_dailymed,
        label_limit=args.label_limit,
        source_dir=args.source_dir,
    )


if __name__ == "__main__":
    main()
