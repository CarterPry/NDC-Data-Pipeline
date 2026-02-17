"""Phase 3: Soft-delete products/packages no longer in source data."""


def soft_delete_missing(conn, seen_product_ndcs: set, seen_package_ndcs: set) -> dict:
    """Soft-delete products and packages not seen in the current source data.

    Args:
        conn: Database connection.
        seen_product_ndcs: Set of product_ndc_raw values from product.txt.
        seen_package_ndcs: Set of package_ndc_raw values from package.txt.

    Returns dict with delete counts.
    """
    stats = {"products_deleted": 0, "packages_deleted": 0}
    cur = conn.cursor()

    # Find and soft-delete products not in source
    if seen_product_ndcs:
        cur.execute("""
            SELECT id, product_ndc_raw FROM products
            WHERE is_current = TRUE AND is_deleted = FALSE
        """)
        for row in cur.fetchall():
            pid, raw_ndc = row
            if raw_ndc not in seen_product_ndcs:
                cur.execute("""
                    UPDATE products
                    SET is_deleted = TRUE, deleted_at = NOW(), updated_at = NOW()
                    WHERE id = %s
                """, (pid,))
                stats["products_deleted"] += 1

    # Find and soft-delete packages not in source
    if seen_package_ndcs:
        cur.execute("""
            SELECT id, package_ndc_raw FROM packages
            WHERE is_current = TRUE AND is_deleted = FALSE
        """)
        for row in cur.fetchall():
            pkg_id, raw_ndc = row
            if raw_ndc not in seen_package_ndcs:
                cur.execute("""
                    UPDATE packages
                    SET is_deleted = TRUE, deleted_at = NOW(), updated_at = NOW()
                    WHERE id = %s
                """, (pkg_id,))
                stats["packages_deleted"] += 1

    conn.commit()
    cur.close()

    print(f"  Cleanup: {stats['products_deleted']} products soft-deleted, "
          f"{stats['packages_deleted']} packages soft-deleted")

    return stats
