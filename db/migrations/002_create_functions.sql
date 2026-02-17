-- NDC normalization function: pads segments to 5-4-2 (package) or 5-4 (product)
CREATE OR REPLACE FUNCTION normalize_ndc(raw_ndc TEXT) RETURNS TEXT AS $$
DECLARE
    parts TEXT[];
BEGIN
    parts := string_to_array(raw_ndc, '-');
    IF array_length(parts, 1) = 3 THEN
        RETURN lpad(parts[1], 5, '0') || '-' || lpad(parts[2], 4, '0') || '-' || lpad(parts[3], 2, '0');
    ELSIF array_length(parts, 1) = 2 THEN
        RETURN lpad(parts[1], 5, '0') || '-' || lpad(parts[2], 4, '0');
    ELSE
        RAISE EXCEPTION 'Invalid NDC format: %', raw_ndc;
    END IF;
END;
$$ LANGUAGE plpgsql IMMUTABLE;
