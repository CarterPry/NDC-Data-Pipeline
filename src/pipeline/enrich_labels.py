"""Phase 2: Enrich products with label text from the openFDA drug/label API."""

import hashlib
import time

import requests
from psycopg.rows import dict_row
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from src import config


def _build_session() -> requests.Session:
    """Build a requests session with retry logic."""
    session = requests.Session()
    retries = Retry(
        total=config.OPENFDA_MAX_RETRIES,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def _content_hash(fields: dict) -> str:
    """Hash label text fields for change detection."""
    raw = "|".join(str(fields.get(k) or "") for k in sorted(fields.keys()))
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


LABEL_FIELDS = [
    "indications_and_usage",
    "warnings",
    "dosage_and_administration",
    "contraindications",
    "adverse_reactions",
    "drug_interactions",
    "description",
    "how_supplied",
    "storage_and_handling",
    "purpose",
]


def _extract_label(result: dict) -> dict:
    """Extract label text fields from an openFDA API result."""
    label = {}
    for field in LABEL_FIELDS:
        val = result.get(field)
        if isinstance(val, list):
            label[field] = "\n\n".join(val)
        elif isinstance(val, str):
            label[field] = val
        else:
            label[field] = None

    label["openfda_spl_id"] = result.get("set_id") or result.get("id")
    label["source_last_updated"] = result.get("effective_time")
    return label


def enrich_labels(conn, limit: int = 0) -> dict:
    """Query openFDA for label data and upsert into the labels table.

    Args:
        conn: Database connection.
        limit: Max number of products to enrich (0 = all).

    Returns dict with counts.
    """
    stats = {"enriched": 0, "skipped": 0, "api_errors": 0, "no_match": 0}

    cur = conn.cursor(row_factory=dict_row)

    # Find products without current labels
    # Use product_ndc_raw (original format) for openFDA queries — openFDA
    # stores NDCs in their original format, not zero-padded.
    query = """
        SELECT p.id, p.product_ndc_raw, p.product_ndc
        FROM products p
        LEFT JOIN labels l ON l.product_id = p.id AND l.is_current = TRUE
        WHERE p.is_current = TRUE
          AND p.is_deleted = FALSE
          AND l.id IS NULL
        ORDER BY p.id
    """
    if limit > 0:
        query += f" LIMIT {limit}"

    cur.execute(query)
    products = cur.fetchall()

    if not products:
        print("  No products need label enrichment.")
        return stats

    print(f"  Found {len(products)} products needing labels.")

    # Load existing label hashes for dedup
    cur.execute("""
        SELECT product_id, content_hash FROM labels WHERE is_current = TRUE
    """)
    existing_hashes = {row["product_id"]: row["content_hash"] for row in cur.fetchall()}

    session = _build_session()

    # Process in batches
    batch_size = config.OPENFDA_BATCH_SIZE
    total_batches = (len(products) + batch_size - 1) // batch_size

    for batch_num in range(total_batches):
        start = batch_num * batch_size
        batch = products[start:start + batch_size]

        # Build NDC → product_id map for this batch
        # Map both raw and normalized forms so we can match openFDA responses
        ndc_to_pid = {}
        for row in batch:
            ndc_to_pid[row["product_ndc_raw"]] = row["id"]
            ndc_to_pid[row["product_ndc"]] = row["id"]
        # Use raw NDCs for the API query (openFDA uses original format)
        ndc_list = [row["product_ndc_raw"] for row in batch]

        # Build openFDA OR query (space-separated OR, URL-encoded by requests)
        ndc_terms = " OR ".join(f'"{ndc}"' for ndc in ndc_list)
        search = f"openfda.product_ndc:({ndc_terms})"

        params = {"search": search, "limit": 100}
        if config.OPENFDA_API_KEY:
            params["api_key"] = config.OPENFDA_API_KEY

        try:
            resp = session.get(config.OPENFDA_LABEL_URL, params=params, timeout=30)

            if resp.status_code == 404:
                # No results for this batch
                stats["no_match"] += len(batch)
                if (batch_num + 1) % 20 == 0:
                    print(f"  Batch {batch_num + 1}/{total_batches}: no matches")
                time.sleep(config.OPENFDA_REQUEST_DELAY)
                continue

            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])

            if not results:
                stats["no_match"] += len(batch)
            else:
                _process_label_results(cur, results, ndc_to_pid, existing_hashes, stats)

        except requests.RequestException as e:
            stats["api_errors"] += 1
            if stats["api_errors"] <= 5:
                print(f"  WARNING: API error on batch {batch_num + 1}: {e}")

        if (batch_num + 1) % 20 == 0:
            conn.commit()
            print(f"  Batch {batch_num + 1}/{total_batches}: "
                  f"{stats['enriched']} enriched, {stats['no_match']} no match")

        time.sleep(config.OPENFDA_REQUEST_DELAY)

    conn.commit()
    cur.close()

    print(f"  Labels: {stats['enriched']} enriched, {stats['no_match']} no match, "
          f"{stats['skipped']} unchanged, {stats['api_errors']} API errors")

    return stats


def _process_label_results(cur, results, ndc_to_pid, existing_hashes, stats):
    """Process openFDA API results and upsert labels."""
    for result in results:
        label_data = _extract_label(result)

        # Find which of our queried NDCs this label covers
        covered_ndcs = result.get("openfda", {}).get("product_ndc", [])
        matched_pids = set()

        for ndc in covered_ndcs:
            pid = ndc_to_pid.get(ndc)
            if pid:
                matched_pids.add(pid)

        for pid in matched_pids:
            chash = _content_hash(label_data)

            # Check if we already have this exact content
            if existing_hashes.get(pid) == chash:
                stats["skipped"] += 1
                continue

            # Expire old label if exists
            cur.execute("""
                UPDATE labels SET is_current = FALSE, valid_to = NOW(), updated_at = NOW()
                WHERE product_id = %s AND is_current = TRUE
            """, (pid,))

            # Insert new label
            cur.execute("""
                INSERT INTO labels (
                    product_id, indications_and_usage, warnings,
                    dosage_and_administration, contraindications, adverse_reactions,
                    drug_interactions, description, how_supplied,
                    storage_and_handling, purpose, openfda_spl_id,
                    source_last_updated, content_hash
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                pid,
                label_data["indications_and_usage"],
                label_data["warnings"],
                label_data["dosage_and_administration"],
                label_data["contraindications"],
                label_data["adverse_reactions"],
                label_data["drug_interactions"],
                label_data["description"],
                label_data["how_supplied"],
                label_data["storage_and_handling"],
                label_data["purpose"],
                label_data["openfda_spl_id"],
                label_data["source_last_updated"],
                chash,
            ))

            existing_hashes[pid] = chash
            stats["enriched"] += 1
