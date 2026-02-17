import pytest
from src.normalize import normalize_ndc, normalize_product_ndc, normalize_package_ndc


class TestNormalizeNDC:
    # 3-segment (package NDC) tests
    def test_4_4_2_format(self):
        assert normalize_ndc("1234-5678-90") == "01234-5678-90"

    def test_5_3_2_format(self):
        assert normalize_ndc("12345-678-90") == "12345-0678-90"

    def test_5_4_1_format(self):
        assert normalize_ndc("12345-6789-0") == "12345-6789-00"

    def test_5_4_2_already_normalized(self):
        assert normalize_ndc("12345-6789-01") == "12345-6789-01"

    # 2-segment (product NDC) tests
    def test_4_4_product(self):
        assert normalize_ndc("1234-5678") == "01234-5678"

    def test_5_3_product(self):
        assert normalize_ndc("12345-678") == "12345-0678"

    def test_5_4_product(self):
        assert normalize_ndc("12345-6789") == "12345-6789"

    # Whitespace handling
    def test_strips_whitespace(self):
        assert normalize_ndc("  1234-5678-90  ") == "01234-5678-90"

    # Error cases
    def test_single_segment_raises(self):
        with pytest.raises(ValueError):
            normalize_ndc("12345")

    def test_four_segments_raises(self):
        with pytest.raises(ValueError):
            normalize_ndc("1-2-3-4")

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            normalize_ndc("")


class TestNormalizeProductNDC:
    def test_4_4(self):
        assert normalize_product_ndc("1234-5678") == "01234-5678"

    def test_5_3(self):
        assert normalize_product_ndc("12345-678") == "12345-0678"

    def test_rejects_3_segment(self):
        with pytest.raises(ValueError):
            normalize_product_ndc("1234-5678-90")


class TestNormalizePackageNDC:
    def test_4_4_2(self):
        assert normalize_package_ndc("1234-5678-90") == "01234-5678-90"

    def test_5_3_2(self):
        assert normalize_package_ndc("12345-678-90") == "12345-0678-90"

    def test_5_4_1(self):
        assert normalize_package_ndc("12345-6789-0") == "12345-6789-00"

    def test_rejects_2_segment(self):
        with pytest.raises(ValueError):
            normalize_package_ndc("1234-5678")


class TestHyphenFreeNDC:
    def test_10_digit_package(self):
        # 00573-0150-20 without hyphens → "0057301502 0" wait, let's be precise
        # 00573-0150-20 → "00573015020" (11 digits, already 5-4-2)
        assert normalize_ndc("00573015020") == "00573-0150-20"

    def test_10_digit_as_package(self):
        # 10 digits → 5-4-2 split: "0573015020" → "05730-1502-0" → "05730-1502-00"
        assert normalize_ndc("0573015020") == "05730-1502-00"

    def test_9_digit_product(self):
        # 9 digits → 5-4 split: "005730150" → "00573-0150"
        assert normalize_ndc("005730150") == "00573-0150"

    def test_rejects_short(self):
        with pytest.raises(ValueError):
            normalize_ndc("12345678")

    def test_rejects_non_digit(self):
        with pytest.raises(ValueError):
            normalize_ndc("abcdefghij")
