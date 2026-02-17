"""Phase 1b: Parse package.txt and upsert into packages table."""

import csv
import hashlib
import os
from datetime import datetime

from psycopg.rows import dict_row

from src.normalize import normalize_package_ndc, normalize_product_ndc


def _parse_date(val: str):
    if not val or val.strip() in ("", "N/A"):
        return None
    try:
        return datetime.strptime(val.strip(), "%Y%m%d").date()
    except ValueError:
        return None


def _compute_hash(fields: list) -> str:
    raw = "|".join(str(f) for f in fields)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


PACKAGE_COLUMNS = [
    "product_id", "package_ndc_raw", "package_ndc", "package_description",
    "start_marketing_date", "end_marketing_date", "sample_package",
    "ndc_exclude_flag", "source_hash",
]


def ingest_packages(extract_dir: str, conn) -> dict:
    """Ingest package.txt into the packages table.

    Returns dict with counts: {inserted, updated, skipped, errors}.
    """
    package_path = os.path.join(extract_dir, "package.txt")
    stats = {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0, "orphans": 0}

    cur = conn.cursor(row_factory=dict_row)

    # Build lookup: normalized product NDC → product id (current, non-deleted)
    cur.execute("""
        SELECT id, product_ndc, product_ndc_raw
        FROM products
        WHERE is_current = TRUE AND is_deleted = FALSE
    """)
    product_lookup = {}
    for row in cur.fetchall():
        product_lookup[row["product_ndc"]] = row["id"]
        product_lookup[row["product_ndc_raw"]] = row["id"]

    # Load existing packages for change detection
    cur.execute("""
        SELECT id, package_ndc_raw, source_hash
        FROM packages
        WHERE is_current = TRUE AND is_deleted = FALSE
    """)
    existing = {row["package_ndc_raw"]: (row["id"], row["source_hash"]) for row in cur.fetchall()}

    seen_raw_ndcs = set()

    with open(package_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f, delimiter="\t")

        batch_inserts = []
        batch_expire_ids = []

        for row_num, row in enumerate(reader, start=2):
            try:
                raw_pkg_ndc = row.get("NDCPACKAGECODE", "").strip()
                raw_prod_ndc = row.get("PRODUCTNDC", "").strip()
                if not raw_pkg_ndc or not raw_prod_ndc:
                    continue

                exclude = row.get("NDC_EXCLUDE_FLAG", "").strip().upper()
                if exclude == "Y":
                    continue

                seen_raw_ndcs.add(raw_pkg_ndc)

                # Normalize
                try:
                    norm_pkg = normalize_package_ndc(raw_pkg_ndc)
                except ValueError:
                    stats["errors"] += 1
                    continue

                try:
                    norm_prod = normalize_product_ndc(raw_prod_ndc)
                except ValueError:
                    stats["errors"] += 1
                    continue

                # Find parent product
                product_id = product_lookup.get(norm_prod) or product_lookup.get(raw_prod_ndc)
                if not product_id:
                    stats["orphans"] += 1
                    continue

                record = {
                    "product_id": product_id,
                    "package_ndc_raw": raw_pkg_ndc,
                    "package_ndc": norm_pkg,
                    "package_description": row.get("PACKAGEDESCRIPTION", "").strip() or None,
                    "start_marketing_date": _parse_date(row.get("STARTMARKETINGDATE", "")),
                    "end_marketing_date": _parse_date(row.get("ENDMARKETINGDATE", "")),
                    "sample_package": row.get("SAMPLE_PACKAGE", "").strip().upper() == "Y",
                    "ndc_exclude_flag": False,
                }

                hash_fields = [record[k] for k in sorted(record.keys())]
                source_hash = _compute_hash(hash_fields)

                if raw_pkg_ndc in existing:
                    old_id, old_hash = existing[raw_pkg_ndc]
                    if old_hash == source_hash:
                        stats["skipped"] += 1
                        continue
                    batch_expire_ids.append(old_id)
                    record["source_hash"] = source_hash
                    batch_inserts.append(record)
                    stats["updated"] += 1
                else:
                    record["source_hash"] = source_hash
                    batch_inserts.append(record)
                    stats["inserted"] += 1

                if len(batch_inserts) >= 2000:
                    try:
                        _flush_packages(cur, batch_inserts, batch_expire_ids)
                        conn.commit()
                    except Exception as e:
                        conn.rollback()
                        stats["errors"] += len(batch_inserts)
                        print(f"  WARNING: Package batch flush failed at row {row_num}: {e}")
                    finally:
                        batch_inserts.clear()
                        batch_expire_ids.clear()

            except Exception as e:
                stats["errors"] += 1
                if stats["errors"] <= 10:
                    print(f"  WARNING: Package row {row_num}: {e}")

    if batch_inserts or batch_expire_ids:
        try:
            _flush_packages(cur, batch_inserts, batch_expire_ids)
            conn.commit()
        except Exception as e:
            conn.rollback()
            stats["errors"] += len(batch_inserts)
            print(f"  WARNING: Final package batch flush failed: {e}")

    cur.close()

    print(f"  Packages: {stats['inserted']} inserted, {stats['updated']} updated, "
          f"{stats['skipped']} unchanged, {stats['orphans']} orphans, {stats['errors']} errors")

    return {**stats, "seen_raw_ndcs": seen_raw_ndcs}


def _flush_packages(cur, inserts, expire_ids):
    """Batch insert new packages and expire old ones."""
    if expire_ids:
        cur.execute("""
            UPDATE packages SET is_current = FALSE, valid_to = NOW(), updated_at = NOW()
            WHERE id = ANY(%s)
        """, (expire_ids,))

    if not inserts:
        return

    placeholders = []
    params = []
    for rec in inserts:
        ph = ", ".join(["%s"] * len(PACKAGE_COLUMNS))
        placeholders.append(f"({ph})")
        params.extend(rec[c] for c in PACKAGE_COLUMNS)

    col_str = ", ".join(PACKAGE_COLUMNS)
    cur.execute(
        f"INSERT INTO packages ({col_str}) VALUES {', '.join(placeholders)}",
        params,
    )
