"""NDC normalization utilities.

FDA NDCs come in variable formats:
  Product NDC (2-segment): 4-4, 5-3, or 5-4
  Package NDC (3-segment): 4-4-2, 5-3-2, or 5-4-1

We normalize to:
  Product NDC: 5-4  (zero-padded)
  Package NDC: 5-4-2 (zero-padded)
"""


def _insert_hyphens(ndc_digits: str) -> str:
    """Try to insert hyphens into a hyphen-free NDC string.

    FDA NDCs are always 10 digits total (without hyphens) in one of three
    segment patterns: 4-4-2, 5-3-2, or 5-4-1.  For a 9-digit product NDC
    the patterns are 4-4 or 5-3 (left-pad to 5-4).

    We normalize all to 5-4-2 (package) or 5-4 (product), so we can
    always assume 5-4-2 for 10 digits and 5-4 for 9 digits after
    zero-padding.
    """
    d = ndc_digits.strip()
    if not d.isdigit():
        raise ValueError(f"Invalid hyphen-free NDC (non-digit chars): {d}")
    if len(d) == 11:
        # Already 5-4-2 with no hyphens
        return f"{d[:5]}-{d[5:9]}-{d[9:]}"
    elif len(d) == 10:
        # Package NDC: treat as 5-4-2 (the normalized width)
        return f"{d[:5]}-{d[5:9]}-{d[9:]}"
    elif len(d) == 9:
        # Product NDC: treat as 5-4
        return f"{d[:5]}-{d[5:]}"
    else:
        raise ValueError(f"Invalid hyphen-free NDC length ({len(d)}): {d}")


def normalize_ndc(ndc: str) -> str:
    """Normalize an NDC to zero-padded format.

    2-segment input → 5-4 output (product NDC)
    3-segment input → 5-4-2 output (package NDC)
    Hyphen-free input (9-11 digits) → auto-detect and normalize

    Raises ValueError for invalid formats.
    """
    cleaned = ndc.strip()
    parts = cleaned.split("-")

    if len(parts) == 3:
        labeler, product, package = parts
        return f"{labeler.zfill(5)}-{product.zfill(4)}-{package.zfill(2)}"
    elif len(parts) == 2:
        labeler, product = parts
        return f"{labeler.zfill(5)}-{product.zfill(4)}"
    elif len(parts) == 1 and cleaned.isdigit() and len(cleaned) >= 9:
        # Hyphen-free NDC — insert hyphens then normalize
        with_hyphens = _insert_hyphens(cleaned)
        return normalize_ndc(with_hyphens)
    else:
        raise ValueError(f"Invalid NDC format: {ndc}")


def normalize_product_ndc(ndc: str) -> str:
    """Normalize a 2-segment product NDC to 5-4 format."""
    parts = ndc.strip().split("-")
    if len(parts) != 2:
        raise ValueError(f"Expected 2-segment product NDC, got: {ndc}")
    labeler, product = parts
    return f"{labeler.zfill(5)}-{product.zfill(4)}"


def normalize_package_ndc(ndc: str) -> str:
    """Normalize a 3-segment package NDC to 5-4-2 format."""
    parts = ndc.strip().split("-")
    if len(parts) != 3:
        raise ValueError(f"Expected 3-segment package NDC, got: {ndc}")
    labeler, product, package = parts
    return f"{labeler.zfill(5)}-{product.zfill(4)}-{package.zfill(2)}"
