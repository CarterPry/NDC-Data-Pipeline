"""Phase 2b: Fill label gaps using DailyMed SPL XML for products openFDA missed."""

import hashlib
import time
import xml.etree.ElementTree as ET

import requests
from psycopg.rows import dict_row
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


DAILYMED_SPL_SEARCH = "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls.json"
DAILYMED_SPL_DETAIL = "https://dailymed.nlm.nih.gov/dailymed/services/v2/spls/{setid}.xml"
REQUEST_DELAY = 0.5

# LOINC codes → our label field names
LOINC_MAP = {
    "34067-9": "indications_and_usage",
    "43685-7": "warnings",
    "34071-1": "warnings",              # WARNINGS SECTION (older format)
    "34068-7": "dosage_and_administration",
    "34070-3": "contraindications",
    "34084-4": "adverse_reactions",
    "34073-7": "drug_interactions",
    "34089-3": "description",
    "34069-5": "how_supplied",
    "44425-7": "storage_and_handling",
    "69718-5": "purpose",
    "59845-8": "purpose",
}

HL7_NS = "{urn:hl7-org:v3}"


def _build_session() -> requests.Session:
    session = requests.Session()
    retries = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def _content_hash(fields: dict) -> str:
    raw = "|".join(str(fields.get(k) or "") for k in sorted(fields.keys()))
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _extract_text(el) -> str:
    """Recursively extract all text content from an XML element."""
    parts = []
    if el.text:
        parts.append(el.text.strip())
    for child in el:
        parts.append(_extract_text(child))
        if child.tail:
            parts.append(child.tail.strip())
    return " ".join(p for p in parts if p)


def _extract_section_text(section) -> str:
    """Get all text from a section and its nested subsections."""
    texts = []
    text_el = section.find(f"{HL7_NS}text")
    if text_el is not None:
        t = _extract_text(text_el)
        if t:
            texts.append(t)
    for sub in section.findall(f"{HL7_NS}component/{HL7_NS}section"):
        t = _extract_section_text(sub)
        if t:
            texts.append(t)
    return "\n\n".join(texts)


def _parse_spl_xml(xml_bytes: bytes) -> dict:
    """Parse SPL XML and extract label fields by LOINC code."""
    label = {
        "indications_and_usage": None,
        "warnings": None,
        "dosage_and_administration": None,
        "contraindications": None,
        "adverse_reactions": None,
        "drug_interactions": None,
        "description": None,
        "how_supplied": None,
        "storage_and_handling": None,
        "purpose": None,
    }

    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return label

    # Extract setid for reference
    setid_el = root.find(f"{HL7_NS}setId")
    if setid_el is not None:
        label["openfda_spl_id"] = setid_el.get("root")

    effective_el = root.find(f"{HL7_NS}effectiveTime")
    if effective_el is not None:
        label["source_last_updated"] = effective_el.get("value")

    for section in root.iter(f"{HL7_NS}section"):
        code_el = section.find(f"{HL7_NS}code")
        if code_el is None:
            continue
        code = code_el.get("code", "")
        field = LOINC_MAP.get(code)
        if not field:
            continue
        # Only set if we haven't already (first match wins for duplicate codes)
        if label.get(field) is not None:
            continue
        text = _extract_section_text(section)
        if text:
            label[field] = text

    return label


def enrich_from_dailymed(conn, limit: int = 0) -> dict:
    """Query DailyMed for label data on products that openFDA missed.

    Args:
        conn: Database connection.
        limit: Max products to process (0 = all).

    Returns dict with counts.
    """
    stats = {"enriched": 0, "no_spl": 0, "no_sections": 0, "api_errors": 0}

    cur = conn.cursor(row_factory=dict_row)

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
        print("  No products need DailyMed enrichment.")
        return stats

    print(f"  Found {len(products)} products without labels — querying DailyMed...")

    # Load existing label hashes
    cur.execute("SELECT product_id, content_hash FROM labels WHERE is_current = TRUE")
    existing_hashes = {row["product_id"]: row["content_hash"] for row in cur.fetchall()}

    session = _build_session()

    for i, product in enumerate(products):
        pid = product["id"]
        raw_ndc = product["product_ndc_raw"]

        try:
            # Step 1: Find setid via SPL search
            resp = session.get(
                DAILYMED_SPL_SEARCH,
                params={"ndc": raw_ndc},
                timeout=15,
            )
            if resp.status_code != 200:
                stats["api_errors"] += 1
                time.sleep(REQUEST_DELAY)
                continue

            data = resp.json().get("data", [])
            if not data:
                stats["no_spl"] += 1
                time.sleep(REQUEST_DELAY)
                continue

            setid = data[0].get("setid")
            if not setid:
                stats["no_spl"] += 1
                time.sleep(REQUEST_DELAY)
                continue

            time.sleep(REQUEST_DELAY)

            # Step 2: Fetch full SPL XML
            xml_url = DAILYMED_SPL_DETAIL.format(setid=setid)
            resp = session.get(xml_url, timeout=30)
            if resp.status_code != 200:
                stats["api_errors"] += 1
                time.sleep(REQUEST_DELAY)
                continue

            label_data = _parse_spl_xml(resp.content)

            # Check if we got anything useful
            label_fields = {k: v for k, v in label_data.items()
                           if k not in ("openfda_spl_id", "source_last_updated") and v is not None}
            if not label_fields:
                stats["no_sections"] += 1
                time.sleep(REQUEST_DELAY)
                continue

            # Content hash dedup
            chash = _content_hash(label_data)
            if existing_hashes.get(pid) == chash:
                time.sleep(REQUEST_DELAY)
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
                label_data.get("indications_and_usage"),
                label_data.get("warnings"),
                label_data.get("dosage_and_administration"),
                label_data.get("contraindications"),
                label_data.get("adverse_reactions"),
                label_data.get("drug_interactions"),
                label_data.get("description"),
                label_data.get("how_supplied"),
                label_data.get("storage_and_handling"),
                label_data.get("purpose"),
                label_data.get("openfda_spl_id"),
                label_data.get("source_last_updated"),
                chash,
            ))

            existing_hashes[pid] = chash
            stats["enriched"] += 1

        except requests.RequestException:
            stats["api_errors"] += 1

        if (i + 1) % 100 == 0:
            conn.commit()
            print(f"  Progress: {i + 1}/{len(products)} — "
                  f"{stats['enriched']} enriched, {stats['no_spl']} no SPL, "
                  f"{stats['api_errors']} errors")

        time.sleep(REQUEST_DELAY)

    conn.commit()
    cur.close()

    print(f"  DailyMed: {stats['enriched']} enriched, {stats['no_spl']} no SPL found, "
          f"{stats['no_sections']} no useful sections, {stats['api_errors']} API errors")

    return stats
