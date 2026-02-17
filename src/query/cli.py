"""Query CLI for the NDC database."""

import argparse
import json
import sys

from psycopg.rows import dict_row

from src.db import get_connection
from src.normalize import normalize_ndc


def _get_product_by_id(cur, product_id: int) -> dict | None:
    cur.execute("""
        SELECT product_ndc, product_ndc_raw, proprietary_name, proprietary_name_suffix,
               nonproprietary_name, dosage_form, route, marketing_category,
               application_number, labeler_name, product_type, pharm_classes,
               dea_schedule, start_marketing_date, end_marketing_date
        FROM products
        WHERE id = %s AND is_current = TRUE
    """, (product_id,))
    row = cur.fetchone()
    if not row:
        return None
    return {k: _serialize(v) for k, v in dict(row).items()}


def _get_packages(cur, product_id: int) -> list:
    cur.execute("""
        SELECT package_ndc, package_ndc_raw, package_description,
               start_marketing_date, end_marketing_date, sample_package
        FROM packages
        WHERE product_id = %s AND is_current = TRUE AND is_deleted = FALSE
        ORDER BY package_ndc
    """, (product_id,))
    return [{k: _serialize(v) for k, v in dict(r).items()} for r in cur.fetchall()]


def _get_ingredients(cur, product_id: int) -> list:
    cur.execute("""
        SELECT substance_name, strength, unit
        FROM active_ingredients
        WHERE product_id = %s AND is_current = TRUE
        ORDER BY substance_name
    """, (product_id,))
    return [dict(r) for r in cur.fetchall()]


def _get_label(cur, product_id: int) -> dict | None:
    cur.execute("""
        SELECT indications_and_usage, warnings, dosage_and_administration,
               contraindications, adverse_reactions, drug_interactions,
               description, how_supplied, storage_and_handling, purpose,
               openfda_spl_id, source_last_updated
        FROM labels
        WHERE product_id = %s AND is_current = TRUE
    """, (product_id,))
    row = cur.fetchone()
    if not row:
        return None
    # Only include non-null fields
    return {k: v for k, v in dict(row).items() if v is not None}


def _serialize(val):
    """Convert date/datetime objects to strings for JSON."""
    if hasattr(val, "isoformat"):
        return val.isoformat()
    return val


def query_by_ndc(ndc_input: str):
    """Look up a package NDC and display full product info."""
    conn = get_connection()
    cur = conn.cursor(row_factory=dict_row)

    # Try normalized and raw forms
    try:
        normalized = normalize_ndc(ndc_input)
    except ValueError:
        normalized = ndc_input

    # Search packages first
    cur.execute("""
        SELECT p.id as product_id, pkg.package_ndc, pkg.package_description
        FROM packages pkg
        JOIN products p ON pkg.product_id = p.id AND p.is_current = TRUE
        WHERE (pkg.package_ndc = %s OR pkg.package_ndc_raw = %s)
          AND pkg.is_current = TRUE AND pkg.is_deleted = FALSE
        LIMIT 1
    """, (normalized, ndc_input))
    row = cur.fetchone()

    # If not found as package, try as product NDC
    if not row:
        cur.execute("""
            SELECT id as product_id
            FROM products
            WHERE (product_ndc = %s OR product_ndc_raw = %s)
              AND is_current = TRUE AND is_deleted = FALSE
            LIMIT 1
        """, (normalized, ndc_input))
        row = cur.fetchone()

    if not row:
        print(json.dumps({"error": f"No product found for NDC: {ndc_input}"}, indent=2))
        cur.close()
        conn.close()
        return

    product_id = row["product_id"]
    result = {
        "product": _get_product_by_id(cur, product_id),
        "active_ingredients": _get_ingredients(cur, product_id),
        "packages": _get_packages(cur, product_id),
        "label": _get_label(cur, product_id),
    }

    print(json.dumps(result, indent=2))
    cur.close()
    conn.close()


def query_by_product(product_ndc_input: str):
    """Look up a product NDC."""
    conn = get_connection()
    cur = conn.cursor(row_factory=dict_row)

    try:
        normalized = normalize_ndc(product_ndc_input)
    except ValueError:
        normalized = product_ndc_input

    cur.execute("""
        SELECT id
        FROM products
        WHERE (product_ndc = %s OR product_ndc_raw = %s)
          AND is_current = TRUE AND is_deleted = FALSE
        LIMIT 1
    """, (normalized, product_ndc_input))
    row = cur.fetchone()

    if not row:
        print(json.dumps({"error": f"No product found for NDC: {product_ndc_input}"}, indent=2))
        cur.close()
        conn.close()
        return

    product_id = row["id"]
    result = {
        "product": _get_product_by_id(cur, product_id),
        "active_ingredients": _get_ingredients(cur, product_id),
        "packages": _get_packages(cur, product_id),
        "label": _get_label(cur, product_id),
    }

    print(json.dumps(result, indent=2))
    cur.close()
    conn.close()


def query_by_search(name: str):
    """Search products by name using full-text search."""
    conn = get_connection()
    cur = conn.cursor(row_factory=dict_row)

    # Try full-text search first
    cur.execute("""
        SELECT id, product_ndc, proprietary_name, nonproprietary_name,
               dosage_form, labeler_name, route
        FROM products
        WHERE to_tsvector('english',
            COALESCE(proprietary_name, '') || ' ' || COALESCE(nonproprietary_name, ''))
            @@ plainto_tsquery('english', %s)
          AND is_current = TRUE AND is_deleted = FALSE
        ORDER BY proprietary_name
        LIMIT 20
    """, (name,))
    rows = cur.fetchall()

    # Fall back to ILIKE if FTS returns nothing
    if not rows:
        pattern = f"%{name}%"
        cur.execute("""
            SELECT id, product_ndc, proprietary_name, nonproprietary_name,
                   dosage_form, labeler_name, route
            FROM products
            WHERE (proprietary_name ILIKE %s OR nonproprietary_name ILIKE %s)
              AND is_current = TRUE AND is_deleted = FALSE
            ORDER BY proprietary_name
            LIMIT 20
        """, (pattern, pattern))
        rows = cur.fetchall()

    if not rows:
        print(json.dumps({"results": [], "message": f"No products found matching: {name}"}, indent=2))
    else:
        results = []
        for row in rows:
            packages = _get_packages(cur, row["id"])
            package_ndcs = [p["package_ndc"] for p in packages]
            results.append({
                "product_ndc": row["product_ndc"],
                "proprietary_name": row["proprietary_name"],
                "nonproprietary_name": row["nonproprietary_name"],
                "dosage_form": row["dosage_form"],
                "labeler_name": row["labeler_name"],
                "route": row["route"],
                "package_ndcs": package_ndcs,
            })
        print(json.dumps({"results": results, "count": len(results)}, indent=2))

    cur.close()
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Query the NDC database")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ndc", help="Look up by package or product NDC")
    group.add_argument("--product", help="Look up by product NDC")
    group.add_argument("--search", help="Search by drug name")

    args = parser.parse_args()

    if args.ndc:
        query_by_ndc(args.ndc)
    elif args.product:
        query_by_product(args.product)
    elif args.search:
        query_by_search(args.search)


if __name__ == "__main__":
    main()
