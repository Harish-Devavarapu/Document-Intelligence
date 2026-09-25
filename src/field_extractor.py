import json
import re
import sys
from pathlib import Path

import pandas as pd


# ============================================================
# UNIVERSAL FIELD EXTRACTOR
# ============================================================
#
# Purpose:
#   Convert OCR/layout/spatial observations into structured JSON.
#
# Design:
#   - Observation-first.
#   - No company/seller/product-specific values.
#   - No fixed document coordinates.
#   - No Amazon-specific product keywords.
#   - No vendor-specific "For <company>" rules.
#   - Reads all available pages.
#   - Preserves page boundaries.
#   - Extracts only values supported by OCR/layout evidence.
#   - Does not invent missing values.
#
# Main output:
#   invoice_data.json
#
# The JSON contains:
#   documents[]  -> page/document-level observations
#   document_count
#   source_pages
#
# A backwards-compatible top-level view is also included when the
# input contains exactly one logical document.
# ============================================================


DEFAULT_WORK_DIR = Path("output")
OUTPUT_FILE_NAME = "invoice_data.json"


# ============================================================
# BASIC HELPERS
# ============================================================

def clean_text(value):
    if value is None:
        return ""

    try:
        if pd.isna(value):
            return ""
    except Exception:
        pass

    text = str(value)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize(value):
    text = clean_text(value).upper()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def numeric_text(value):
    text = clean_text(value)
    return text.replace("₹", "").replace("$", "").replace("€", "").replace("£", "")


def money_values(text):
    """
    Return monetary-looking decimal values.

    This function only observes OCR text. It does not infer which value
    represents a business field.
    """
    text = clean_text(text)

    pattern = r"[-−]?\s*(?:₹|Rs\.?|INR|\$|€|£)?\s*\d{1,3}(?:,\d{3})*(?:\.\d{2})"

    values = []

    for match in re.findall(pattern, text, flags=re.IGNORECASE):
        value = re.sub(r"\s+", "", match)
        value = value.replace("−", "-")
        value = re.sub(r"^(?:₹|Rs\.?|INR|\$|€|£)", "", value, flags=re.I)
        value = value.replace(",", "")
        values.append(value)

    return values


def first_match(text, patterns):
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return clean_text(match.group(1))
    return ""


def all_matches(text, pattern):
    return [clean_text(x) for x in re.findall(pattern, text, flags=re.IGNORECASE)]


def unique(values):
    result = []
    seen = set()

    for value in values:
        value = clean_text(value)

        if not value:
            continue

        key = normalize(value)

        if key in seen:
            continue

        seen.add(key)
        result.append(value)

    return result


# ============================================================
# WORK DIRECTORY
# ============================================================

def get_work_dir():
    if len(sys.argv) > 1 and sys.argv[1].strip():
        work_dir = Path(sys.argv[1].strip())
    else:
        work_dir = DEFAULT_WORK_DIR

    if not work_dir.exists():
        raise FileNotFoundError(
            f"Working directory not found:\n{work_dir}"
        )

    return work_dir


# ============================================================
# DATA LOADING
# ============================================================

def load_csv_if_exists(path):
    if not path.exists():
        return pd.DataFrame()

    try:
        return pd.read_csv(path)
    except Exception as exc:
        raise RuntimeError(
            f"Could not read CSV:\n{path}\n{exc}"
        ) from exc


def find_layout_file(work_dir):
    candidates = [
        work_dir / "invoice_blocks_with_sections_all_pages.csv",
        work_dir / "invoice_blocks_with_sections.csv",
    ]

    for path in candidates:
        if path.exists():
            return path

    return None


def load_observations(work_dir):
    """
    Load spatial output first and merge missing layout observations.

    spatial_parser.py intentionally emits classified regions. Layout
    analysis retains the complete block set, so layout is used to restore
    observations that spatial parsing did not assign to a region.
    """
    spatial_path = work_dir / "spatial_regions.csv"
    layout_path = find_layout_file(work_dir)

    if not spatial_path.exists():
        raise FileNotFoundError(
            f"Spatial file not found:\n{spatial_path}\n\n"
            "Run spatial_parser.py before field extraction."
        )

    spatial = load_csv_if_exists(spatial_path)
    layout = load_csv_if_exists(layout_path) if layout_path else pd.DataFrame()

    if spatial.empty and layout.empty:
        raise ValueError("No OCR/layout observations were found.")

    if spatial.empty:
        combined = layout.copy()
    elif layout.empty:
        combined = spatial.copy()
    else:
        combined = spatial.copy()

        # Preserve every observation from layout that is absent in spatial.
        key_columns = []

        if "page_number" in spatial.columns and "page_number" in layout.columns:
            key_columns = ["page_number", "block_number"]
        elif "page" in spatial.columns and "page" in layout.columns:
            key_columns = ["page", "block_number"]
        elif "block_number" in spatial.columns and "block_number" in layout.columns:
            key_columns = ["block_number"]

        if key_columns:
            spatial_keys = set(
                tuple(row)
                for row in spatial[key_columns].astype(str).itertuples(
                    index=False,
                    name=None,
                )
            )

            missing_rows = []

            for _, row in layout.iterrows():
                key = tuple(
                    str(row.get(column, ""))
                    for column in key_columns
                )

                if key not in spatial_keys:
                    missing_rows.append(row)

            if missing_rows:
                combined = pd.concat(
                    [combined, pd.DataFrame(missing_rows)],
                    ignore_index=True,
                )

    # Guarantee expected columns.
    if "text" not in combined.columns:
        raise ValueError(
            "OCR text column 'text' is missing from spatial/layout output."
        )

    if "spatial_region" not in combined.columns:
        combined["spatial_region"] = ""

    if "section" not in combined.columns:
        combined["section"] = ""

    for column in ["x_start", "y_start", "x_end", "y_end"]:
        if column not in combined.columns:
            combined[column] = 0.0

    combined["text"] = combined["text"].apply(clean_text)
    combined["section"] = combined["section"].apply(clean_text)
    combined["spatial_region"] = combined["spatial_region"].apply(clean_text)

    for column in ["x_start", "y_start", "x_end", "y_end"]:
        combined[column] = pd.to_numeric(
            combined[column],
            errors="coerce",
        ).fillna(0.0)

    return combined


# ============================================================
# PAGE HANDLING
# ============================================================

def detect_page_column(df):
    candidates = [
        "page_number",
        "page",
        "page_num",
        "page_index",
    ]

    for column in candidates:
        if column in df.columns:
            return column

    return None


def split_pages(df):
    """
    Return [(page_key, page_dataframe), ...].

    If page metadata is unavailable, the entire file is treated as one
    observation set.
    """
    page_column = detect_page_column(df)

    if page_column is None:
        page = df.copy()
        page["_page_key"] = 1
        return [(1, page)]

    values = []

    for value in df[page_column].tolist():
        if pd.isna(value):
            continue

        if value not in values:
            values.append(value)

    def page_sort_key(value):
        try:
            return (0, int(value))
        except Exception:
            return (1, str(value))

    values = sorted(values, key=page_sort_key)

    pages = []

    for value in values:
        page = df[df[page_column] == value].copy()
        page["_page_key"] = value
        pages.append((value, page))

    return pages


# ============================================================
# REGION / SECTION HANDLING
# ============================================================

def normalize_region_name(value):
    value = normalize(value)
    value = re.sub(r"[^A-Z0-9]+", "_", value)
    return value.strip("_")


def region_mask(df, region_name):
    target = normalize_region_name(region_name)

    masks = []

    for column in ["spatial_region", "section"]:
        if column in df.columns:
            masks.append(
                df[column].apply(normalize_region_name) == target
            )

    if not masks:
        return pd.Series(False, index=df.index)

    combined = masks[0].copy()

    for mask in masks[1:]:
        combined = combined | mask

    return combined


def get_region(df, region_name):
    mask = region_mask(df, region_name)

    result = df[mask].copy()

    if result.empty:
        return result

    if "block_number" in result.columns:
        dedupe_columns = ["block_number"]

        if detect_page_column(result):
            dedupe_columns.insert(0, detect_page_column(result))

        result = result.drop_duplicates(
            subset=dedupe_columns,
            keep="first",
        )

    return sort_observations(result)


def sort_observations(df):
    if df.empty:
        return df

    columns = []

    if "y_start" in df.columns:
        columns.append("y_start")

    if "x_start" in df.columns:
        columns.append("x_start")

    if columns:
        return df.sort_values(columns)

    return df


def region_text(df, region_name):
    region = get_region(df, region_name)

    if region.empty:
        return ""

    return " ".join(
        clean_text(value)
        for value in region["text"].tolist()
        if clean_text(value)
    )


# ============================================================
# GENERIC LABELED VALUE EXTRACTION
# ============================================================

def extract_labeled_value(text, labels, stop_labels=None):
    """
    Extract text following one of several labels.

    This is label-driven rather than company/template-driven.
    """
    text = clean_text(text)

    if not text:
        return ""

    label_expression = "|".join(
        re.escape(label)
        for label in labels
    )

    if stop_labels:
        stop_expression = "|".join(
            re.escape(label)
            for label in stop_labels
        )
        pattern = (
            rf"(?:{label_expression})\s*[:#\-]?\s*"
            rf"(.+?)(?=\s+(?:{stop_expression})\s*[:#\-]?\s*|$)"
        )
    else:
        pattern = (
            rf"(?:{label_expression})\s*[:#\-]?\s*(.+)$"
        )

    return first_match(text, [pattern])


# ============================================================
# INVOICE / ORDER
# ============================================================

def extract_invoice(df):
    text = region_text(df, "INVOICE_DETAILS")

    return {
        "invoice_number": first_match(
            text,
            [
                r"\bInvoice\s+(?:Number|No\.?)\s*[:#\-]?\s*([A-Z0-9][A-Z0-9./_-]*)",
            ],
        ),
        "invoice_details": first_match(
            text,
            [
                r"\bInvoice\s+Details?\s*[:#\-]?\s*(.+?)(?=\s+Invoice\s+Date\b|$)",
            ],
        ),
        "invoice_date": first_match(
            text,
            [
                r"\bInvoice\s+Date\s*[:#\-]?\s*([0-9]{1,4}[./-][0-9]{1,2}[./-][0-9]{1,4})",
            ],
        ),
    }


def extract_order(df):
    text = region_text(df, "ORDER")

    return {
        "order_number": first_match(
            text,
            [
                r"\bOrder\s+(?:Number|No\.?|ID)\s*[:#\-]?\s*([A-Z0-9][A-Z0-9./_-]*)",
            ],
        ),
        "order_date": first_match(
            text,
            [
                r"\bOrder\s+Date\s*[:#\-]?\s*([0-9]{1,4}[./-][0-9]{1,2}[./-][0-9]{1,4})",
            ],
        ),
    }


# ============================================================
# SELLER / TAX
# ============================================================

def extract_seller(df):
    region = get_region(df, "SELLER")
    tax_region = get_region(df, "SELLER_TAX")

    lines = [
        clean_text(value)
        for value in region["text"].tolist()
        if clean_text(value)
    ]

    label_pattern = re.compile(
        r"^(?:Sold\s+By|Seller|Supplier|Vendor)\s*[:\-]?$",
        flags=re.IGNORECASE,
    )

    lines = [
        line
        for line in lines
        if not label_pattern.fullmatch(line)
    ]

    seller_name = lines[0] if lines else ""

    address_lines = []

    for line in lines[1:]:
        if normalize(line) in {"IN", "INDIA"}:
            continue

        if seller_name and normalize(line) == normalize(seller_name):
            continue

        # Keep all remaining observations; the downstream validator can
        # decide whether they form a complete address.
        address_lines.append(line)

    tax_text = " ".join(
        clean_text(value)
        for value in tax_region["text"].tolist()
        if clean_text(value)
    )

    pan = first_match(
        tax_text,
        [
            r"\bPAN\s*(?:No\.?|Number)?\s*[:#\-]?\s*([A-Z0-9]{8,20})",
        ],
    )

    gstin = first_match(
        tax_text,
        [
            r"\bGST(?:IN)?\s*(?:Registration\s+No\.?|No\.?|Number)?"
            r"\s*[:#\-]?\s*([A-Z0-9]{10,20})",
        ],
    )

    return {
        "name": seller_name,
        "address": ", ".join(address_lines),
        "pan": pan,
        "gstin": gstin,
    }


# ============================================================
# BILLING / SHIPPING
# ============================================================

def extract_address_region(df, region_name):
    region = get_region(df, region_name)

    if region.empty:
        return {
            "name": "",
            "address": "",
            "state_code": "",
        }

    lines = [
        clean_text(value)
        for value in region["text"].tolist()
        if clean_text(value)
    ]

    label_pattern = re.compile(
        r"^(?:Billing|Shipping)\s+Address\s*[:\-]?$",
        flags=re.IGNORECASE,
    )

    lines = [
        line
        for line in lines
        if not label_pattern.fullmatch(line)
    ]

    name = lines[0] if lines else ""
    address_lines = []
    state_code = ""

    for line in lines[1:]:
        state_match = re.search(
            r"\bState\s*/?\s*UT\s*Code\s*[:\-]?\s*(\d{1,3})",
            line,
            flags=re.IGNORECASE,
        )

        if state_match:
            state_code = state_match.group(1)
            continue

        if normalize(line) in {"IN", "INDIA"}:
            continue

        if name and normalize(line) == normalize(name):
            continue

        address_lines.append(line)

    return {
        "name": name,
        "address": ", ".join(address_lines),
        "state_code": state_code,
    }


# ============================================================
# SUPPLY / DELIVERY
# ============================================================

def extract_supply_delivery(df):
    text = region_text(df, "SUPPLY_DELIVERY")

    return {
        "place_of_supply": first_match(
            text,
            [
                r"\bPlace\s+of\s+Supply\s*[:\-]?\s*(.+?)(?=\s+Place\s+of\s+Delivery\b|$)",
            ],
        ),
        "place_of_delivery": first_match(
            text,
            [
                r"\bPlace\s+of\s+Delivery\s*[:\-]?\s*(.+)$",
            ],
        ),
    }


# ============================================================
# PRODUCT / LINE ITEM EXTRACTION
# ============================================================

def find_product_region(df):
    region = get_region(df, "PRODUCT_TABLE")

    if region.empty:
        return df.copy()

    return sort_observations(region)


def find_hsn(text):
    return first_match(
        text,
        [
            r"\bHSN\s*[:#\-]?\s*(\d{4,12})\b",
        ],
    )


def find_sac(text):
    return first_match(
        text,
        [
            r"\bSAC\s*(?:Code|No\.?|Number)?\s*[:#\-]?\s*(\d{4,12})\b",
        ],
    )


def find_product_identifier(text):
    """
    Extract common identifier observations without requiring a specific
    vendor's identifier system.

    ASIN is recognized only when an explicit ASIN label exists. A bare
    BXXXXXXXXX token is preserved as an identifier candidate but is not
    automatically called an ASIN.
    """
    explicit = first_match(
        text,
        [
            r"\bASIN\s*[:#\-]?\s*([A-Z0-9]{10})\b",
            r"\bSKU\s*[:#\-]?\s*([A-Z0-9._/-]+)",
            r"\bItem\s+(?:Code|ID|Number)\s*[:#\-]?\s*([A-Z0-9._/-]+)",
            r"\bProduct\s+(?:Code|ID|Number)\s*[:#\-]?\s*([A-Z0-9._/-]+)",
        ],
    )

    if explicit:
        return explicit, "explicit"

    return "", ""


def extract_quantity(text):
    candidates = []

    # Prefer explicit quantity labels.
    for pattern in [
        r"\b(?:Qty|Quantity)\s*[:#\-]?\s*([0-9]+(?:\.[0-9]+)?)\b",
    ]:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)

    # Generic standalone integer candidates. Reject numbers next to letters,
    # percentages, decimals, and obvious identifiers.
    for match in re.finditer(
        r"(?<![A-Za-z0-9.,])([0-9]{1,3})(?![A-Za-z0-9.,])",
        text,
    ):
        value = match.group(1)

        before = text[max(0, match.start() - 10):match.start()]
        after = text[match.end():match.end() + 10]

        if "%" in before or "%" in after:
            continue

        if re.search(r"[A-Za-z]", before[-1:]) or re.search(
            r"[A-Za-z]",
            after[:1],
        ):
            continue

        candidates.append(value)

    return candidates[0] if candidates else ""


def extract_explicit_tax(text):
    """
    Extract explicit tax labels, rates, and amounts.

    No standard tax rate is assumed when OCR does not contain one.
    """
    tax_types = unique(
        re.findall(
            r"\b(CGST|SGST|IGST|UTGST|VAT|Sales\s+Tax)\b",
            text,
            flags=re.IGNORECASE,
        )
    )

    rates = unique(
        re.findall(
            r"\b(\d+(?:\.\d+)?)\s*%",
            text,
            flags=re.IGNORECASE,
        )
    )

    amounts = []

    for tax_type in tax_types:
        matches = re.finditer(
            rf"\b{re.escape(tax_type)}\b",
            text,
            flags=re.IGNORECASE,
        )

        for match in matches:
            after = text[match.end():]
            values = money_values(after[:100])

            if values:
                amounts.append(values[0])

    return {
        "tax_type": "/".join(tax_types),
        "tax_rate": "/".join(
            f"{rate}%"
            for rate in rates
        ),
        "tax_amount": unique(amounts)[0] if amounts else "",
    }


def extract_product(df):
    """
    Extract one or more line-item observations from the product table.

    The function intentionally favors explicit labels and spatial grouping
    over product-name keyword lists.
    """
    table = find_product_region(df)

    if table.empty:
        return []

    rows = []

    for _, row in table.iterrows():
        text = clean_text(row.get("text", ""))

        if not text:
            continue

        upper = normalize(text)

        # Ignore obvious table headers.
        if re.fullmatch(
            r"(?:DESCRIPTION|ITEM|PRODUCT|QTY|QUANTITY|RATE|PRICE|"
            r"AMOUNT|TAX|TOTAL|DISCOUNT|UNIT\s+PRICE|NET\s+AMOUNT|"
            r"HSN|SAC)(?:\s+.*)?",
            upper,
        ):
            continue

        if upper in {
            "NET TAX TAX TAX TOTAL",
            "NET TAX TOTAL",
        }:
            continue

        rows.append(
            {
                "text": text,
                "x_start": float(row.get("x_start", 0)),
                "y_start": float(row.get("y_start", 0)),
                "x_end": float(row.get("x_end", 0)),
                "y_end": float(row.get("y_end", 0)),
            }
        )

    if not rows:
        return []

    # Group nearby observations into horizontal/vertical line-item bands.
    # The gap is derived from the observed y positions, not document
    # coordinates.
    rows.sort(key=lambda item: (item["y_start"], item["x_start"]))

    y_values = [
        row["y_start"]
        for row in rows
    ]

    positive_gaps = [
        y_values[index + 1] - y_values[index]
        for index in range(len(y_values) - 1)
        if y_values[index + 1] > y_values[index]
    ]

    if positive_gaps:
        median_gap = float(pd.Series(positive_gaps).median())
    else:
        median_gap = 50.0

    band_gap = max(18.0, min(median_gap * 0.9, 90.0))

    bands = []

    for row in rows:
        if not bands:
            bands.append([row])
            continue

        previous = bands[-1][-1]

        if abs(row["y_start"] - previous["y_start"]) <= band_gap:
            bands[-1].append(row)
        else:
            bands.append([row])

    # Select bands containing meaningful product/amount observations.
    candidates = []

    for band in bands:
        text = " ".join(row["text"] for row in band)
        upper = normalize(text)

        if "SHIPPING CHARGES" in upper:
            continue

        if re.search(
            r"\b(?:CGST|SGST|IGST|UTGST|VAT|SALES\s+TAX)\b",
            upper,
        ) and not re.search(
            r"\b(?:DESCRIPTION|PRODUCT|ITEM|HSN|SAC|SKU|ASIN)\b",
            upper,
        ):
            # Tax-only continuation rows can still be part of the item,
            # but they should not be selected alone.
            continue

        values = money_values(text)

        identifier, identifier_source = find_product_identifier(text)
        hsn = find_hsn(text)
        sac = find_sac(text)

        words = re.findall(r"[A-Za-z]{2,}", text)

        score = 0

        if values:
            score += 20

        if len(words) >= 2:
            score += 15

        if identifier:
            score += 20

        if hsn:
            score += 20

        if sac:
            score += 15

        if re.search(
            r"\b(?:Qty|Quantity)\b",
            text,
            flags=re.IGNORECASE,
        ):
            score += 10

        if re.search(
            r"\b(?:CGST|SGST|IGST|UTGST|VAT|Sales\s+Tax)\b",
            text,
            flags=re.IGNORECASE,
        ):
            score += 5

        if score:
            candidates.append(
                (
                    score,
                    band,
                    text,
                    values,
                    identifier,
                    identifier_source,
                    hsn,
                    sac,
                )
            )

    if not candidates:
        return []

    # Highest-scoring band becomes the primary line item.
    candidates.sort(
        key=lambda item: item[0],
        reverse=True,
    )

    (
        _score,
        primary_band,
        primary_text,
        values,
        identifier,
        identifier_source,
        hsn,
        sac,
    ) = candidates[0]

    # Include nearby continuation bands that look like the same item.
    primary_y = min(
        row["y_start"]
        for row in primary_band
    )

    nearby_texts = [primary_text]

    for candidate in candidates[1:]:
        band = candidate[1]
        band_y = min(row["y_start"] for row in band)

        if abs(band_y - primary_y) <= max(
            band_gap * 2,
            90.0,
        ):
            nearby_texts.append(candidate[2])

    combined_text = clean_text(
        " ".join(nearby_texts)
    )

    if not identifier:
        identifier, identifier_source = find_product_identifier(
            combined_text
        )

    if not hsn:
        hsn = find_hsn(combined_text)

    if not sac:
        sac = find_sac(combined_text)

    quantity = extract_quantity(combined_text)
    tax = extract_explicit_tax(combined_text)

    # Explicitly labeled price/amount fields are preferred.
    unit_price = first_match(
        combined_text,
        [
            r"\b(?:Unit\s+Price|Rate|Price)\s*[:#\-]?\s*"
            r"(?:₹|Rs\.?|INR|\$|€|£)?\s*"
            r"([0-9,]+\.\d{2})",
        ],
    )

    if not unit_price and values:
        # The first observed monetary value is retained as an observation,
        # not asserted as a business field by a company-specific rule.
        unit_price = values[0]

    discount = first_match(
        combined_text,
        [
            r"\bDiscount\s*[:#\-]?\s*"
            r"(?:₹|Rs\.?|INR|\$|€|£)?\s*([0-9,]+\.\d{2})",
        ],
    )

    net_amount = first_match(
        combined_text,
        [
            r"\b(?:Net\s+Amount|Net)\s*[:#\-]?\s*"
            r"(?:₹|Rs\.?|INR|\$|€|£)?\s*([0-9,]+\.\d{2})",
        ],
    )

    total_amount = first_match(
        combined_text,
        [
            r"\b(?:Line\s+Total|Item\s+Total|Amount)\s*[:#\-]?\s*"
            r"(?:₹|Rs\.?|INR|\$|€|£)?\s*([0-9,]+\.\d{2})",
        ],
    )

    if not total_amount and values:
        total_amount = values[-1]

    description = combined_text

    # Remove common structural labels while preserving actual description.
    description = re.sub(
        r"\b(?:HSN|SAC|SKU|ASIN|Qty|Quantity|Rate|Price|"
        r"Unit\s+Price|Net\s+Amount|Line\s+Total|Item\s+Total)\b"
        r"\s*[:#\-]?\s*[A-Z0-9._/-]*",
        "",
        description,
        flags=re.IGNORECASE,
    )

    description = re.sub(
        r"\b(?:CGST|SGST|IGST|UTGST|VAT|Sales\s+Tax)\b"
        r"\s*(?:\d+(?:\.\d+)?\s*%)?\s*"
        r"(?:₹|Rs\.?|INR|\$|€|£)?\s*[0-9,]+(?:\.\d{2})?",
        "",
        description,
        flags=re.IGNORECASE,
    )

    description = re.sub(
        r"\b(?:TOTAL|SUBTOTAL|DISCOUNT)\b.*$",
        "",
        description,
        flags=re.IGNORECASE,
    )

    description = re.sub(
        r"[-−]?\s*(?:₹|Rs\.?|INR|\$|€|£)?\s*\d{1,3}"
        r"(?:,\d{3})*(?:\.\d{2})",
        "",
        description,
    )

    description = clean_text(description)

    # If the cleanup removed everything, use the strongest text-bearing
    # observation rather than fabricating a product description.
    if not description:
        text_candidates = [
            candidate[2]
            for candidate in candidates
            if len(re.findall(r"[A-Za-z]{2,}", candidate[2])) >= 2
        ]

        if text_candidates:
            description = clean_text(text_candidates[0])

    item = {
        "description": description,
        "quantity": quantity,
        "unit_price": unit_price.replace(",", "") if unit_price else "",
        "tax_rate": tax["tax_rate"],
        "tax_type": tax["tax_type"],
        "tax_amount": tax["tax_amount"],
        "total_amount": total_amount.replace(",", "") if total_amount else "",
        "hsn": hsn,
        "sac": sac,
    }

    if identifier:
        item["product_identifier"] = identifier
        item["product_identifier_source"] = identifier_source

    if discount:
        item["discount"] = discount.replace(",", "")

    if net_amount:
        item["net_amount"] = net_amount.replace(",", "")

    return [item]


# ============================================================
# TOTALS
# ============================================================

def extract_totals(df):
    """
    Extract document-level totals from observations.

    Total labels and monetary values are often split across OCR blocks or
    columns.  This extractor therefore uses the observed text plus page
    geometry, rather than depending on a particular spatial-region label or
    document template.

    Priority:
      1. Explicit total label + amount in the same observation.
      2. Explicit total label + nearest monetary observation on the same
         horizontal line / nearby row.
      3. Monetary observation inside an explicitly TOTAL-labelled region.
      4. No guessing from an arbitrary "largest" amount.
    """
    region = get_region(df, "TOTAL")
    region_text_value = region_text(df, "TOTAL")

    tax_amount = ""
    total_amount = ""

    # ------------------------------------------------------------------
    # Helpers local to totals.  Keep the generic money parser unchanged
    # because it is also used by product extraction.
    # ------------------------------------------------------------------
    def row_amounts(value):
        return money_values(value)

    def numeric_amounts(value):
        """
        Slightly more permissive amount extraction for a row already known
        to be associated with an explicit TOTAL label.  This allows OCR
        values such as 539 or 539.00 while still avoiding dates/identifiers
        unless the row is explicitly tied to TOTAL.
        """
        text = clean_text(value)
        if not text:
            return []

        values = money_values(text)

        # Also accept plain decimal/integer values in a TOTAL-associated row.
        # Decimal values are preferred.  Integer support is useful for OCR
        # where ".00" is dropped.
        if not values:
            matches = re.findall(
                r"(?<![\w/.-])-?\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?(?![\w/.-])",
                text,
            )
            for match in matches:
                cleaned = match.replace(",", "")
                try:
                    number = float(cleaned)
                except Exception:
                    continue

                # Avoid treating very small integers such as a quantity as
                # the invoice total when the row contains other text.
                if number >= 10 or "." in cleaned or "," in match:
                    values.append(cleaned)

        return values

    def is_total_label(value):
        text = clean_text(value)
        if not text:
            return False

        # Explicit total labels.  "TOTAL TAX" is deliberately excluded.
        return bool(
            re.search(
                r"\b(?:GRAND\s+TOTAL|INVOICE\s+TOTAL|TOTAL\s+AMOUNT|"
                r"AMOUNT\s+(?:DUE|PAYABLE)|BALANCE\s+DUE)\b",
                text,
                flags=re.IGNORECASE,
            )
            or re.search(
                r"(?<![A-Z])TOTAL(?!\s+TAX(?:ES)?\b)",
                text,
                flags=re.IGNORECASE,
            )
        )

    # ------------------------------------------------------------------
    # 1. Explicit label/value pairs inside the TOTAL region.
    # ------------------------------------------------------------------
    total_amount = first_match(
        region_text_value,
        [
            r"(?:Grand\s+Total|Total\s+Amount|Invoice\s+Total|"
            r"Amount\s+(?:Due|Payable)|Balance\s+Due)"
            r"\s*[:#\-]?\s*(?:₹|Rs\.?|INR|\$|€|£)?\s*"
            r"([0-9,]+(?:\.\d{1,2})?)",
            r"\bTOTAL\s*[:#\-]?\s*(?:₹|Rs\.?|INR|\$|€|£)?\s*"
            r"([0-9,]+(?:\.\d{1,2})?)",
        ],
    )

    tax_amount = first_match(
        region_text_value,
        [
            r"(?:Total\s+Tax(?:es)?|Tax\s+Amount|Tax\s+Total)"
            r"\s*[:#\-]?\s*(?:₹|Rs\.?|INR|\$|€|£)?\s*"
            r"([0-9,]+(?:\.\d{1,2})?)",
        ],
    )

    # ------------------------------------------------------------------
    # 2. Locate every explicit TOTAL-labelled observation on this page.
    # ------------------------------------------------------------------
    label_rows = []

    for idx, row in df.iterrows():
        row_text = clean_text(row.get("text", ""))
        if row_text and is_total_label(row_text):
            label_rows.append((idx, row))

    if not total_amount and label_rows:
        # The final explicit total label is normally the document total.
        label_idx, total_row = label_rows[-1]

        try:
            total_y = float(total_row.get("y_start", 0))
        except Exception:
            total_y = 0.0

        try:
            total_x = float(total_row.get("x_start", 0))
        except Exception:
            total_x = 0.0

        try:
            total_x_end = float(total_row.get("x_end", total_x))
        except Exception:
            total_x_end = total_x

        candidates = []

        for idx, row in df.iterrows():
            if idx == label_idx:
                continue

            row_text = clean_text(row.get("text", ""))
            values = numeric_amounts(row_text)
            if not values:
                continue

            try:
                row_y = float(row.get("y_start", 0))
            except Exception:
                continue

            try:
                row_x = float(row.get("x_start", 0))
            except Exception:
                row_x = 0.0

            try:
                row_x_end = float(row.get("x_end", row_x))
            except Exception:
                row_x_end = row_x

            dy = abs(row_y - total_y)

            # Horizontal relationship:
            #   - same/overlapping row is strongest
            #   - a value to the right of TOTAL is common
            #   - a value immediately before TOTAL is also common in
            #     reconstructed OCR tables.
            same_line = dy <= max(
                12.0,
                abs(float(total_row.get("y_end", total_y)) - total_y),
            )

            if same_line:
                if row_x >= total_x_end:
                    horizontal_distance = row_x - total_x_end
                elif row_x_end <= total_x:
                    horizontal_distance = total_x - row_x_end
                else:
                    horizontal_distance = 0.0

                # Strong preference for a value on the same visual row.
                # When several amounts share the row, prefer the amount
                # positioned to the right of the TOTAL label; invoice
                # layouts commonly place the payable value in the final
                # right-hand column while tax values appear earlier.
                direction_rank = 0 if row_x >= total_x_end else 1
                score = (0, direction_rank, horizontal_distance)
            else:
                # Nearby rows are secondary.
                horizontal_distance = abs(row_x - total_x)
                score = (1, dy, horizontal_distance)

            # Avoid very distant unrelated monetary rows.
            if dy > 120 and not same_line:
                continue

            candidates.append(
                (
                    score,
                    row_y,
                    row_x,
                    values[-1],
                )
            )

        if candidates:
            candidates.sort(key=lambda item: item[0])
            total_amount = candidates[0][3]

    # ------------------------------------------------------------------
    # 3. If TOTAL is represented by a region but the label/value are split
    #    into separate blocks, use the nearest amount within that region.
    # ------------------------------------------------------------------
    if not total_amount and not region.empty:
        label_candidates = []

        for idx, row in region.iterrows():
            if is_total_label(row.get("text", "")):
                try:
                    y = float(row.get("y_start", 0))
                except Exception:
                    y = 0.0
                label_candidates.append((idx, y))

        if label_candidates:
            label_idx, label_y = label_candidates[-1]
            candidates = []

            for idx, row in region.iterrows():
                if idx == label_idx:
                    continue

                values = numeric_amounts(row.get("text", ""))
                if not values:
                    continue

                try:
                    y = float(row.get("y_start", 0))
                except Exception:
                    continue

                candidates.append((abs(y - label_y), y, values[-1]))

            if candidates:
                candidates.sort(key=lambda item: item[0])
                total_amount = candidates[0][2]

        # A TOTAL region may contain only the amount because the label was
        # assigned to another region.  In that case, use its last observed
        # monetary value, but only because the region itself is explicitly
        # marked TOTAL by the upstream spatial parser.
        if not total_amount:
            regional_amounts = []
            for _, row in region.iterrows():
                values = numeric_amounts(row.get("text", ""))
                if values:
                    try:
                        y = float(row.get("y_start", 0))
                    except Exception:
                        y = 0.0
                    regional_amounts.append((y, values[-1]))

            if regional_amounts:
                regional_amounts.sort(key=lambda item: item[0])
                total_amount = regional_amounts[-1][1]

    # ------------------------------------------------------------------
    # 4. Do NOT select an arbitrary page-wide amount if no explicit total
    #    observation exists.  That would be guessing.
    # ------------------------------------------------------------------
    return {
        "tax_amount": tax_amount.replace(",", "") if tax_amount else "",
        "total_amount": total_amount.replace(",", "") if total_amount else "",
    }


# ============================================================
# AMOUNT IN WORDS
# ============================================================

def extract_amount_in_words(df):
    region = get_region(df, "AMOUNT_IN_WORDS")

    if region.empty:
        return ""

    lines = [
        clean_text(value)
        for value in region["text"].tolist()
        if clean_text(value)
    ]

    result = []

    started = False

    for line in lines:
        if re.search(
            r"\bAmount\s+in\s+Words\b",
            line,
            flags=re.IGNORECASE,
        ):
            started = True

            remaining = re.sub(
                r"\bAmount\s+in\s+Words\b\s*[:#\-]?",
                "",
                line,
                flags=re.IGNORECASE,
            )

            remaining = clean_text(remaining)

            if remaining:
                result.append(remaining)

            continue

        if not started:
            continue

        if re.match(
            r"^For\s+.+:\s*$",
            line,
            flags=re.IGNORECASE,
        ):
            break

        if re.search(
            r"\bAuthorized\s+Signatory\b|\bAuthorised\s+Signatory\b",
            line,
            flags=re.IGNORECASE,
        ):
            break

        result.append(line)

    return clean_text(" ".join(result))


# ============================================================
# REVERSE CHARGE
# ============================================================

def extract_reverse_charge(df):
    text = region_text(df, "REVERSE_CHARGE")

    return first_match(
        text,
        [
            r"\bReverse\s+Charge\s*[:#\-]?\s*(Yes|No)\b",
        ],
    )


# ============================================================
# PAYMENT
# ============================================================

def extract_payment(df):
    region = get_region(df, "PAYMENT")

    if region.empty:
        return {
            "transaction_id": "",
            "date_time": "",
            "invoice_value": "",
            "mode": "",
        }

    text = region_text(df, "PAYMENT")

    transaction_id = first_match(
        text,
        [
            r"\bPayment\s+Transaction\s+ID\s*[:#\-]?\s*"
            r"([A-Z0-9._/-]{4,})",
            r"\bTransaction\s+ID\s*[:#\-]?\s*"
            r"([A-Z0-9._/-]{4,})",
            r"\bTransaction\s+Reference\s*[:#\-]?\s*"
            r"([A-Z0-9._/-]{4,})",
        ],
    )

    date_time = first_match(
        text,
        [
            r"\bDate\s*(?:&|and)\s*Time\s*[:#\-]?\s*"
            r"([0-9./-]+(?:\s*,\s*|\s+)[0-9:]+)",
            r"\bPayment\s+Date\s*[:#\-]?\s*"
            r"([0-9./-]+)",
        ],
    )

    invoice_value = first_match(
        text,
        [
            r"\bInvoice\s+Value\s*[:#\-]?\s*"
            r"(?:₹|Rs\.?|INR|\$|€|£)?\s*([0-9,]+\.\d{2})",
            r"\bPayment\s+Amount\s*[:#\-]?\s*"
            r"(?:₹|Rs\.?|INR|\$|€|£)?\s*([0-9,]+\.\d{2})",
        ],
    )

    # Payment mode is intentionally captured as the value following a
    # generic label instead of restricting it to a fixed list of modes.
    mode = first_match(
        text,
        [
            r"\bMode\s+of\s+Payment\s*[:#\-]?\s*([A-Za-z][A-Za-z0-9 /_-]{1,40})",
            r"\bPayment\s+Method\s*[:#\-]?\s*([A-Za-z][A-Za-z0-9 /_-]{1,40})",
            r"\bPayment\s+Mode\s*[:#\-]?\s*([A-Za-z][A-Za-z0-9 /_-]{1,40})",
        ],
    )

    return {
        "transaction_id": transaction_id,
        "date_time": date_time,
        "invoice_value": invoice_value.replace(",", "")
        if invoice_value
        else "",
        "mode": clean_text(mode),
    }


# ============================================================
# LOGICAL DOCUMENT GROUPING
# ============================================================

def non_empty_fields(record):
    if not isinstance(record, dict):
        return 0

    count = 0

    for value in record.values():
        if isinstance(value, str) and value.strip():
            count += 1
        elif isinstance(value, list) and value:
            count += 1
        elif isinstance(value, dict) and non_empty_fields(value):
            count += 1

    return count


def extract_page_document(page_df, page_number):
    """
    Extract one page-level document observation.

    Page-level extraction is deliberate: a multi-page PDF can contain
    multiple related invoices or different sellers. Combining them into
    one seller/invoice record would create false data.
    """
    invoice = extract_invoice(page_df)
    order = extract_order(page_df)
    seller = extract_seller(page_df)
    billing = extract_address_region(page_df, "BILLING")
    shipping = extract_address_region(page_df, "SHIPPING")
    supply_delivery = extract_supply_delivery(page_df)
    items = extract_product(page_df)
    totals = extract_totals(page_df)

    amount_in_words = extract_amount_in_words(page_df)
    reverse_charge = extract_reverse_charge(page_df)
    payment = extract_payment(page_df)

    source_blocks = len(page_df)

    record = {
        "page": page_number,
        "invoice": invoice,
        "order": order,
        "seller": seller,
        "billing": billing,
        "shipping": shipping,
        "supply_delivery": supply_delivery,
        "items": items,
        "totals": totals,
        "amount_in_words": amount_in_words,
        "reverse_charge": reverse_charge,
        "payment": payment,
        "source_observation_count": source_blocks,
    }

    return record


# ============================================================
# BACKWARD-COMPATIBLE TOP-LEVEL VIEW
# ============================================================

def build_output(documents):
    """
    Preserve a familiar invoice_data.json shape when there is one
    logical page/document, while keeping the authoritative page-aware
    documents[] collection for multi-page input.
    """
    result = {
        "document_count": len(documents),
        "documents": documents,
        "source_pages": [
            document.get("page")
            for document in documents
        ],
    }

    if len(documents) == 1:
        document = documents[0]

        result.update(
            {
                "invoice": document.get("invoice", {}),
                "order": document.get("order", {}),
                "seller": document.get("seller", {}),
                "billing": document.get("billing", {}),
                "shipping": document.get("shipping", {}),
                "supply_delivery": document.get(
                    "supply_delivery",
                    {},
                ),
                "items": document.get("items", []),
                "totals": document.get("totals", {}),
                "reverse_charge": document.get(
                    "reverse_charge",
                    "",
                ),
                "payment": document.get("payment", {}),
            }
        )

        result["totals"]["amount_in_words"] = document.get(
            "amount_in_words",
            "",
        )

    return result


# ============================================================
# MAIN
# ============================================================

def main():
    work_dir = get_work_dir()

    print("=" * 75)
    print("UNIVERSAL FIELD EXTRACTION")
    print("=" * 75)

    print(f"\nWorking directory : {work_dir}")

    df = load_observations(work_dir)

    print(f"Loaded observations : {len(df)}")

    pages = split_pages(df)

    print(f"Detected pages      : {len(pages)}")

    documents = []

    for page_number, page_df in pages:
        print("\n" + "-" * 75)
        print(f"PROCESSING PAGE {page_number}")
        print("-" * 75)

        document = extract_page_document(
            page_df,
            page_number,
        )

        documents.append(document)

        print(
            f"Observations : {document['source_observation_count']}"
        )
        print(
            f"Invoice No.  : "
            f"{document['invoice'].get('invoice_number', '')}"
        )
        print(
            f"Seller       : "
            f"{document['seller'].get('name', '')}"
        )
        print(
            f"Items        : "
            f"{len(document.get('items', []))}"
        )
        print(
            f"Total        : "
            f"{document['totals'].get('total_amount', '')}"
        )

    output = build_output(documents)

    output_path = work_dir / OUTPUT_FILE_NAME

    with output_path.open(
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            output,
            file,
            indent=4,
            ensure_ascii=False,
        )

    print("\n" + "=" * 75)
    print("FIELD EXTRACTION COMPLETE")
    print("=" * 75)

    print(f"\nDocuments extracted : {len(documents)}")
    print(f"Saved JSON          : {output_path}")


if __name__ == "__main__":
    main()
