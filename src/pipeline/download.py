"""Download and extract the FDA NDC directory zip file."""

import hashlib
import os
import shutil
import zipfile

import requests

from src import config


def download_ndc_zip(tmp_dir: str) -> tuple[str, str]:
    """Download ndctext.zip and return (extract_dir, file_md5).

    Returns the path to the extracted directory and the MD5 hash of the zip.
    """
    os.makedirs(tmp_dir, exist_ok=True)
    zip_path = os.path.join(tmp_dir, "ndctext.zip")
    extract_dir = os.path.join(tmp_dir, "ndcdata")

    print(f"  Downloading {config.NDC_ZIP_URL} ...")
    resp = requests.get(config.NDC_ZIP_URL, stream=True, timeout=120)
    resp.raise_for_status()

    md5 = hashlib.md5()
    with open(zip_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=8192):
            f.write(chunk)
            md5.update(chunk)

    file_hash = md5.hexdigest()
    print(f"  Downloaded ({os.path.getsize(zip_path)} bytes, MD5: {file_hash})")

    # Extract
    if os.path.exists(extract_dir):
        shutil.rmtree(extract_dir)
    os.makedirs(extract_dir)

    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(extract_dir)

    # Validate expected files exist
    product_path = os.path.join(extract_dir, "product.txt")
    package_path = os.path.join(extract_dir, "package.txt")

    if not os.path.exists(product_path):
        raise FileNotFoundError(f"product.txt not found in {extract_dir}")
    if not os.path.exists(package_path):
        raise FileNotFoundError(f"package.txt not found in {extract_dir}")

    print(f"  Extracted to {extract_dir}")
    return extract_dir, file_hash


def check_source_unchanged(file_hash: str, cur) -> bool:
    """Return True if the last successful pipeline run used the same source hash."""
    cur.execute("""
        SELECT source_file_hash FROM pipeline_runs
        WHERE status = 'completed'
        ORDER BY completed_at DESC
        LIMIT 1
    """)
    row = cur.fetchone()
    if row and row[0] == file_hash:
        return True
    return False
