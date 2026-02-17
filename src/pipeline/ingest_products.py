"""Phase 1: Parse product.txt and upsert into products + active_ingredients tables."""

import csv
import hashlib
import os
from datetime import datetime

from psycopg.rows import dict_row

from src.normalize import normalize_product_ndc


def _parse_date(val: str):
    """Parse FDA date string (YYYYMMDD) to date or None."""
    if not val or val.strip() in ("", "N/A"):
        return None
    try:
        return datetime.strptime(val.strip(), "%Y%m%d").date()
    except ValueError:
        return None


def _compute_hash(fields: list) -> str:
    """Compute MD5 of concatenated field values for change detection."""
    raw = "|".join(str(f) for f in fields)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _parse_ingredients(substance_str, strength_str, unit_str):
    """Split semicolon-delimited ingredient fields into aligned tuples."""
    substances = [s.strip() for s in (substance_str or "").split(";")] if substance_str else []
    strengths = [s.strip() for s in (strength_str or "").split(";")] if strength_str else []
    units = [s.strip() for s in (unit_str or "").split(";")] if unit_str else []

    # Pad shorter lists with empty strings
    max_len = max(len(substances), len(strengths), len(units), 1)
    substances += [""] * (max_len - len(substances))
    strengths += [""] * (max_len - len(strengths))
    units += [""] * (max_len - len(units))

    result = []
    for name, strength, unit in zip(substances, strengths, units):
        if name:  # Only include rows with an actual substance name
            result.append((name, strength or None, unit or None))
    return result


PRODUCT_COLUMNS = [
    "product_ndc_raw", "product_ndc", "product_type", "proprietary_name",
    "proprietary_name_suffix", "nonproprietary_name", "dosage_form", "route",
    "marketing_category", "application_number", "labeler_name", "pharm_classes",
    "dea_schedule", "listing_certified_through", "start_marketing_date",
    "end_marketing_date", "ndc_exclude_flag", "source_hash",
]


def ingest_products(extract_dir: str, conn) -> dict:
    """Ingest product.txt into the products and active_ingredients tables.

    Returns dict with counts: {inserted, updated, skipped, errors}.
    """
    product_path = os.path.join(extract_dir, "product.txt")
    stats = {"inserted": 0, "updated": 0, "skipped": 0, "errors": 0}

    # Load existing current products by raw NDC for change detection
    cur = conn.cursor(row_factory=dict_row)
    cur.execute("""
        SELECT id, product_ndc_raw, source_hash
        FROM products
        WHERE is_current = TRUE AND is_deleted = FALSE
    """)
    existing = {row["product_ndc_raw"]: (row["id"], row["source_hash"]) for row in cur.fetchall()}

    # Track which raw NDCs we see in this run (for soft-delete detection later)
    seen_raw_ndcs = set()

    with open(product_path, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f, delimiter="\t")

        batch_inserts = []
        batch_updates = []
        ingredient_ops = []  # (product_ndc_raw, ingredients_list)

        for row_num, row in enumerate(reader, start=2):
            try:
                raw_ndc = row.get("PRODUCTNDC", "").strip()
                if not raw_ndc:
                    continue

                exclude = row.get("NDC_EXCLUDE_FLAG", "").strip().upper()
                if exclude == "Y":
                    continue

                seen_raw_ndcs.add(raw_ndc)

                # Normalize
                try:
                    normalized = normalize_product_ndc(raw_ndc)
                except ValueError:
                    stats["errors"] += 1
                    continue

                # Build record
                record = {
                    "product_ndc_raw": raw_ndc,
                    "product_ndc": normalized,
                    "product_type": row.get("PRODUCTTYPENAME", "").strip() or None,
                    "proprietary_name": row.get("PROPRIETARYNAME", "").strip() or None,
                    "proprietary_name_suffix": row.get("PROPRIETARYNAMESUFFIX", "").strip() or None,
                    "nonproprietary_name": row.get("NONPROPRIETARYNAME", "").strip() or None,
                    "dosage_form": row.get("DOSAGEFORMNAME", "").strip() or None,
                    "route": row.get("ROUTENAME", "").strip() or None,
                    "marketing_category": row.get("MARKETINGCATEGORYNAME", "").strip() or None,
                    "application_number": row.get("APPLICATIONNUMBER", "").strip() or None,
                    "labeler_name": row.get("LABELERNAME", "").strip() or None,
                    "pharm_classes": row.get("PHARM_CLASSES", "").strip() or None,
                    "dea_schedule": row.get("DEASCHEDULE", "").strip() or None,
                    "listing_certified_through": _parse_date(row.get("LISTING_RECORD_CERTIFIED_THROUGH", "")),
                    "start_marketing_date": _parse_date(row.get("STARTMARKETINGDATE", "")),
                    "end_marketing_date": _parse_date(row.get("ENDMARKETINGDATE", "")),
                    "ndc_exclude_flag": exclude == "Y",
                }

                # Source hash for change detection
                hash_fields = [record[k] for k in sorted(record.keys())]
                source_hash = _compute_hash(hash_fields)

                # Ingredients
                ingredients = _parse_ingredients(
                    row.get("SUBSTANCENAME", ""),
                    row.get("ACTIVE_NUMERATOR_STRENGTH", ""),
                    row.get("ACTIVE_INGRED_UNIT", ""),
                )

                # Check existing
                if raw_ndc in existing:
                    old_id, old_hash = existing[raw_ndc]
                    if old_hash == source_hash:
                        stats["skipped"] += 1
                        continue
                    # Changed — expire old, queue new
                    batch_updates.append(old_id)
                    record["source_hash"] = source_hash
                    batch_inserts.append(record)
                    ingredient_ops.append((raw_ndc, ingredients))
                    stats["updated"] += 1
                else:
                    # New product
                    record["source_hash"] = source_hash
                    batch_inserts.append(record)
                    ingredient_ops.append((raw_ndc, ingredients))
                    stats["inserted"] += 1

                # Flush batches periodically
                if len(batch_inserts) >= 2000:
                    try:
                        _flush_products(cur, batch_inserts, batch_updates, ingredient_ops)
                        conn.commit()
                    except Exception as e:
                        conn.rollback()
                        stats["errors"] += len(batch_inserts)
                        print(f"  WARNING: Batch flush failed at row {row_num}: {e}")
                    finally:
                        batch_inserts.clear()
                        batch_updates.clear()
                        ingredient_ops.clear()

            except Exception as e:
                stats["errors"] += 1
                if stats["errors"] <= 10:
                    print(f"  WARNING: Error on row {row_num}: {e}")

    # Final flush
    if batch_inserts or batch_updates:
        try:
            _flush_products(cur, batch_inserts, batch_updates, ingredient_ops)
            conn.commit()
        except Exception as e:
            conn.rollback()
            stats["errors"] += len(batch_inserts)
            print(f"  WARNING: Final batch flush failed: {e}")

    cur.close()

    print(f"  Products: {stats['inserted']} inserted, {stats['updated']} updated, "
          f"{stats['skipped']} unchanged, {stats['errors']} errors")

    return {**stats, "seen_raw_ndcs": seen_raw_ndcs}


def _flush_products(cur, inserts, update_ids, ingredient_ops):
    """Batch insert new product rows and expire old ones."""
    # Expire old versions
    if update_ids:
        cur.execute("""
            UPDATE products SET is_current = FALSE, valid_to = NOW(), updated_at = NOW()
            WHERE id = ANY(%s)
        """, (update_ids,))
        cur.execute("""
            UPDATE active_ingredients SET is_current = FALSE, valid_to = NOW()
            WHERE product_id = ANY(%s) AND is_current = TRUE
        """, (update_ids,))

    if not inserts:
        return

    # Build multi-row INSERT with RETURNING
    placeholders = []
    params = []
    for rec in inserts:
        ph = ", ".join(["%s"] * len(PRODUCT_COLUMNS))
        placeholders.append(f"({ph})")
        params.extend(rec[c] for c in PRODUCT_COLUMNS)

    col_str = ", ".join(PRODUCT_COLUMNS)
    query = f"""
        INSERT INTO products ({col_str})
        VALUES {", ".join(placeholders)}
        RETURNING id, product_ndc_raw
    """
    cur.execute(query, params)
    result = cur.fetchall()

    # Build map of raw_ndc → new product id
    ndc_to_id = {row["product_ndc_raw"]: row["id"] for row in result}

    # Insert ingredients for new/updated products
    ingredient_values = []
    ingredient_params = []
    for raw_ndc, ingredients in ingredient_ops:
        pid = ndc_to_id.get(raw_ndc)
        if not pid:
            continue
        for substance, strength, unit in ingredients:
            ingredient_values.append("(%s, %s, %s, %s)")
            ingredient_params.extend([pid, substance, strength, unit])

    if ingredient_values:
        cur.execute(
            f"INSERT INTO active_ingredients (product_id, substance_name, strength, unit) VALUES {', '.join(ingredient_values)}",
            ingredient_params,
        )
