"""
Universal spatial/table extractor.

Purpose
-------
Reconstruct line items and charge rows from OCR/layout observations without
assuming a particular company, invoice template, field coordinate, or product.

Input:
    <work_dir>/spatial_regions.csv

Output:
    <work_dir>/extracted_items.csv

The extractor is observation-first:
    OCR observations -> page isolation -> table rows -> columns/amount roles

It preserves uncertain values rather than inventing them.  Page boundaries are
kept throughout so observations from separate invoices in a multi-page PDF
cannot leak into one another.
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_WORK_DIR = BASE_DIR / "output"

if len(sys.argv) > 1 and sys.argv[1].strip():
    WORK_DIR = Path(sys.argv[1]).expanduser()
    if not WORK_DIR.is_absolute():
        WORK_DIR = BASE_DIR / WORK_DIR
else:
    WORK_DIR = DEFAULT_WORK_DIR

SPATIAL_REGIONS_FILE = WORK_DIR / "spatial_regions.csv"
OUTPUT_FILE = WORK_DIR / "extracted_items.csv"

AMOUNT_TOLERANCE = 0.02
ROW_GAP_FACTOR = 4.5


# ---------------------------------------------------------------------------
# Basic parsing helpers
# ---------------------------------------------------------------------------

def parse_amounts(text: str) -> List[str]:
    if not text:
        return []

    # Monetary values must have two decimal places.  Negative values are
    # retained because discounts/credits can legitimately be negative.
    pattern = r"(?<![\d.])[-−]?\d[\d,]*\.\d{2}(?![\d.])"
    return [m.replace(",", "").replace("−", "-") for m in re.findall(pattern, str(text))]


def parse_percentages(text: str) -> List[float]:
    if not text:
        return []
    values = []
    for match in re.findall(r"(\d+(?:\.\d+)?)\s*%", str(text)):
        try:
            values.append(float(match))
        except ValueError:
            pass
    return values


def parse_quantity_tokens(text: str) -> List[int]:
    """
    Extract integer quantity tokens while rejecting:
      - decimal fragments
      - percentages
      - digits embedded in ASIN/SKU/HSN/product codes
    """
    if not text:
        return []

    values: List[int] = []
    pattern = r"(?<![A-Za-z0-9.])(\d{1,3})(?![A-Za-z0-9.])"

    for match in re.finditer(pattern, str(text)):
        start, end = match.span(1)
        before = str(text)[max(0, start - 2):start]
        after = str(text)[end:end + 2]

        if "%" in before or "%" in after:
            continue

        try:
            value = int(match.group(1))
        except ValueError:
            continue

        if 1 <= value <= 999:
            values.append(value)

    return values


def parse_asin(text: str) -> Optional[str]:
    if not text:
        return None
    match = re.search(r"\bB[0-9A-Z]{9}\b", str(text).upper())
    return match.group(0) if match else None


def parse_hsn(text: str) -> Optional[str]:
    if not text:
        return None

    value = str(text).strip()
    explicit = re.search(r"\bHSN\s*[:\-]?\s*(\d{6,8})\b", value, re.I)
    if explicit:
        return explicit.group(1)

    if re.fullmatch(r"\d{6,8}", value):
        return value

    return None


def approximately_equal(first: float, second: float) -> bool:
    return abs(first - second) <= AMOUNT_TOLERANCE


def normalize_text(text: str) -> str:
    value = str(text or "").lower()
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def is_header_text(text: str) -> bool:
    normalized = normalize_text(text)
    if not normalized:
        return False

    strong_patterns = [
        r"^no\.?\s+description$",
        r"^description$",
        r"^sl\.?\s*no\.?\s+description$",
    ]
    if any(re.fullmatch(pattern, normalized) for pattern in strong_patterns):
        return True

    terms = [
        "description", "unit price", "price", "qty", "quantity",
        "net amount", "tax rate", "tax type", "tax amount", "total amount",
        "discount",
    ]
    found = sum(term in normalized for term in terms)
    return found >= 2


def is_metadata_text(text: str) -> bool:
    value = normalize_text(text)
    if not value:
        return True

    upper = value.upper()

    if parse_hsn(text):
        return True
    if parse_asin(text):
        # A block containing only an ASIN/product-code marker is metadata.
        if not re.search(r"[A-Za-z]{3,}", value.replace("asin", "")):
            return True

    metadata_markers = [
        "HSN:", "HSN ", "CGST", "SGST", "IGST", "UTGST",
        "TAX RATE", "TAX TYPE", "TAX AMOUNT",
    ]
    return any(marker in upper for marker in metadata_markers)


def looks_like_amount_in_words(text: str) -> bool:
    """Identify an amount-in-words observation without relying on a template."""
    value = normalize_text(text)
    if not value:
        return False
    if not re.search(r"\bonly\b", value):
        return False

    number_words = {
        "zero", "one", "two", "three", "four", "five", "six", "seven",
        "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
        "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
        "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
        "hundred", "thousand", "lakh", "lakhs", "million", "billion", "and",
        "only",
    }
    tokens = re.findall(r"[a-z]+", value)
    if not tokens:
        return False
    return sum(token in number_words for token in tokens) / len(tokens) >= 0.75


def clean_description(text: str) -> Optional[str]:
    if not text:
        return None

    value = str(text).strip()
    if looks_like_amount_in_words(value):
        return None

    # Remove row/serial prefixes, but preserve the actual description.
    value = re.sub(
        r"^\s*(?:[|I]\s*)?(?:of\s*)?\d+\s*(?:\||\))?\s*",
        "",
        value,
        flags=re.I,
    )
    value = re.sub(r"\s+", " ", value).strip(" |")

    # OCR can attach column headers to the product description when the
    # layout classifier puts adjacent header observations in the same region.
    # Remove only header-like phrases, never document/company/product values.
    header_patterns = [
        r"\bunit\s+description\s+qty\b",
        r"\bunit\s+description\b",
        r"\bsl\.?\s*no\.?\s+description\b",
        r"\bunit\s+price\s+qty\s+net\s+amount\b",
        r"\bnet\s+amount\s+tax\s+rate\s+tax\s+type\s+tax\s+amount\s+total\s+amount\b",
        r"\bdescription\s+qty\b",
    ]
    for pattern in header_patterns:
        value = re.sub(pattern, " ", value, flags=re.I)

    value = re.sub(r"\s{2,}", " ", value).strip(" |")
    return value or None


# ---------------------------------------------------------------------------
# Spatial observation handling
# ---------------------------------------------------------------------------

def detect_page_column(fieldnames: Iterable[str]) -> Optional[str]:
    preferred = [
        "page_number", "page", "page_num", "page_no",
        "source_page", "document_page",
    ]
    lowered = {name.lower(): name for name in fieldnames}
    for candidate in preferred:
        if candidate in lowered:
            return lowered[candidate]
    return None


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def load_blocks() -> Tuple[List[Dict[str, Any]], Optional[str], List[Dict[str, Any]]]:
    """Load PRODUCT_TABLE blocks plus all page-level observations.

    PRODUCT_TABLE is used to reconstruct rows, while all spatial observations
    on the same page are available for metadata/tax/total values that the
    layout classifier may have placed in another semantic region.
    """
    if not SPATIAL_REGIONS_FILE.exists():
        raise FileNotFoundError(
            f"\nSpatial regions file not found:\n{SPATIAL_REGIONS_FILE}\n\n"
            "Please run spatial_parser.py first."
        )

    with SPATIAL_REGIONS_FILE.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        page_column = detect_page_column(fieldnames)

        required = {"spatial_region", "block_number", "x_start", "x_end",
                    "y_start", "y_end", "text"}
        missing = required - set(fieldnames)
        if missing:
            raise ValueError(
                "spatial_regions.csv is missing required columns: "
                + ", ".join(sorted(missing))
            )

        product_blocks: List[Dict[str, Any]] = []
        all_blocks: List[Dict[str, Any]] = []

        for row in reader:
            text = (row.get("text") or "").strip()
            if not text:
                continue

            try:
                block_number = int(float(row["block_number"]))
            except (TypeError, ValueError):
                continue

            block = {
                "block_number": block_number,
                "x_start": to_float(row["x_start"]),
                "x_end": to_float(row["x_end"]),
                "y_start": to_float(row["y_start"]),
                "y_end": to_float(row["y_end"]),
                "text": text,
                "page": str(row.get(page_column, "1") if page_column else "1"),
                "spatial_region": (row.get("spatial_region") or "").strip(),
            }
            block["x_center"] = (block["x_start"] + block["x_end"]) / 2
            block["y_center"] = (block["y_start"] + block["y_end"]) / 2
            all_blocks.append(block)

            if block["spatial_region"] == "PRODUCT_TABLE":
                product_blocks.append(block)

    return product_blocks, page_column, all_blocks

def group_by_page(blocks: List[Dict[str, Any]]) -> List[Tuple[str, List[Dict[str, Any]]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for block in blocks:
        page = str(block.get("page", "1"))
        grouped.setdefault(page, []).append(block)

    result = []
    for page, page_blocks in grouped.items():
        page_blocks.sort(key=lambda b: (b["y_center"], b["x_start"]))
        result.append((page, page_blocks))

    def page_sort_key(item: Tuple[str, List[Dict[str, Any]]]):
        try:
            return (0, int(float(item[0])))
        except ValueError:
            return (1, item[0])

    return sorted(result, key=page_sort_key)


# ---------------------------------------------------------------------------
# Row detection
# ---------------------------------------------------------------------------

def table_geometry(blocks: List[Dict[str, Any]]) -> Tuple[float, float, float]:
    left = min((b["x_start"] for b in blocks), default=0.0)
    right = max((b["x_end"] for b in blocks), default=0.0)
    width = max(right - left, 1.0)
    return left, right, width


def looks_like_data_description(block: Dict[str, Any], left: float, width: float) -> bool:
    text = block["text"].strip()
    if not text or is_header_text(text):
        return False

    upper = text.upper()
    if "SHIPPING CHARGES" in upper:
        return True

    if parse_hsn(text):
        return False

    # A pure ASIN/product-code block is metadata.
    if parse_asin(text) and len(re.sub(r"[^A-Za-z]", "", text)) <= 2:
        return False

    if not re.search(r"[A-Za-z]{3,}", text):
        return False

    relative_x = (block["x_start"] - left) / width
    if relative_x > 0.58:
        return False

    alpha = len(re.findall(r"[A-Za-z]", text))
    if len(text) and alpha / len(text) < 0.30:
        return False

    return True


def find_row_starts(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    left, _, width = table_geometry(blocks)
    candidates = [
        block for block in blocks
        if looks_like_data_description(block, left, width)
    ]

    # Do not create separate rows from wrapped description lines.
    starts: List[Dict[str, Any]] = []
    for candidate in candidates:
        if not starts:
            starts.append(candidate)
            continue

        previous = starts[-1]
        gap = candidate["y_start"] - previous["y_end"]

        # If this is a nearby left-side continuation, it belongs to the
        # current row. A large vertical gap indicates a new line item.
        same_description_area = candidate["x_start"] <= left + width * 0.60
        if gap <= 65 and same_description_area:
            continue

        starts.append(candidate)

    return starts


def build_rows(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not blocks:
        return []

    starts = find_row_starts(blocks)
    if not starts:
        return []

    rows: List[Dict[str, Any]] = []

    for index, start in enumerate(starts):
        next_start_y = (
            starts[index + 1]["y_start"]
            if index + 1 < len(starts)
            else float("inf")
        )

        # Row content normally spans a few wrapped OCR lines. Keep the
        # grouping relative to the observed line height rather than document
        # coordinates.
        row_height = max(start["y_end"] - start["y_start"], 1.0)
        max_y = min(
            next_start_y - 1,
            start["y_start"] + max(120.0, row_height * ROW_GAP_FACTOR),
        )

        row_blocks = [
            block for block in blocks
            if block["y_end"] >= start["y_start"]
            and block["y_start"] <= max_y
        ]

        # If a shipping/charge row starts, it naturally becomes its own row.
        rows.append({
            "start": start,
            "blocks": sorted(
                row_blocks,
                key=lambda b: (b["y_center"], b["x_start"])
            ),
        })

    return rows


# ---------------------------------------------------------------------------
# Row-level extraction
# ---------------------------------------------------------------------------

def extract_description(row: Dict[str, Any]) -> Optional[str]:
    start = row["start"]
    parts: List[str] = []

    for block in row["blocks"]:
        text = block["text"].strip()
        if not text:
            continue

        upper = text.upper()

        if block is start:
            cleaned = clean_description(text)
            if cleaned:
                parts.append(cleaned)
            continue

        if "SHIPPING CHARGES" in upper:
            if parts:
                break
            cleaned = clean_description(text)
            if cleaned:
                parts.append(cleaned)
            break

        if parse_hsn(text):
            continue

        # Do not append tax/amount-only metadata.
        if any(token in upper for token in [
            "CGST", "SGST", "IGST", "UTGST",
        ]):
            continue

        # Preserve useful product/charge continuation text.
        if re.search(r"[A-Za-z]{3,}", text):
            cleaned = clean_description(text)
            if cleaned:
                parts.append(cleaned)

    if not parts:
        return None

    return clean_description(" ".join(parts))


def row_text(row: Dict[str, Any]) -> str:
    return " ".join(block["text"] for block in row["blocks"])


def extract_quantity(row: Dict[str, Any]) -> Optional[str]:
    # Quantity should be taken from the numeric/data side, not from the
    # description. Reject percentages, decimal numbers and code fragments.
    candidates: List[Tuple[float, int]] = []

    for block in row["blocks"]:
        text = block["text"]
        for value in parse_quantity_tokens(text):
            # Ignore the serial number at the very beginning of a description
            # row when it is the only token in a left-side description block.
            if block["x_center"] < (
                min(b["x_start"] for b in row["blocks"])
                + 0.45 * (
                    max(b["x_end"] for b in row["blocks"])
                    - min(b["x_start"] for b in row["blocks"])
                )
            ):
                if re.fullmatch(r"\s*(?:[|I]\s*)?\d+\s*(?:\||\))?\s*.*", text):
                    # If the block contains actual description words, its
                    # leading number is a row serial, not quantity.
                    if re.search(r"[A-Za-z]{3,}", text):
                        continue

            candidates.append((block["x_center"], value))

    if not candidates:
        return None

    # Prefer the right-side occurrence. In normal invoice tables quantity is
    # after price/discount and before net amount.
    candidates.sort(key=lambda item: item[0])
    return str(candidates[-1][1])


def explicit_discount(row: Dict[str, Any]) -> Optional[str]:
    for block in row["blocks"]:
        text = block["text"]
        if re.search(r"discount", text, re.I):
            amounts = parse_amounts(text)
            if amounts:
                return amounts[0]

        for match in re.finditer(
            r"(?<![\d])[-−]\s*(\d[\d,]*\.\d{2})",
            text,
        ):
            return match.group(1).replace(",", "")

    return None


def nearby_page_blocks(
    row: Dict[str, Any],
    page_blocks: Optional[List[Dict[str, Any]]],
    tolerance: float = 85.0,
) -> List[Dict[str, Any]]:
    """Return same-page observations in the row's local spatial band."""
    if not page_blocks:
        return []

    row_top = min(b["y_start"] for b in row["blocks"])
    row_bottom = max(b["y_end"] for b in row["blocks"])
    result = []

    for block in page_blocks:
        if block in row["blocks"]:
            continue
        if block["y_end"] >= row_top - tolerance and block["y_start"] <= row_bottom + tolerance:
            result.append(block)

    return result


def tax_amount_neighbors(
    tax_block: Dict[str, Any],
    candidates: List[Dict[str, Any]],
) -> List[Tuple[float, str]]:
    """Find monetary observations spatially associated with a tax label."""
    results: List[Tuple[float, str]] = []
    tx = tax_block["x_end"]
    ty = tax_block["y_center"]

    right_side: List[Tuple[float, str]] = []
    fallback: List[Tuple[float, str]] = []
    for block in candidates:
        if block is tax_block:
            continue
        amounts = parse_amounts(block["text"])
        if not amounts:
            continue
        vertical = abs(block["y_center"] - ty)
        signed_horizontal = block["x_start"] - tx
        if vertical > 70.0:
            continue
        distance = vertical + 0.15 * abs(signed_horizontal)
        target = right_side if signed_horizontal >= 0 else fallback
        for amount in amounts:
            target.append((distance, amount))

    # Tax labels normally precede their amount column. Prefer right-side
    # observations; only fall back to the left when no right-side amount exists.
    return sorted(right_side or fallback, key=lambda item: item[0])


def total_amount_neighbors(
    total_block: Dict[str, Any],
    candidates: List[Dict[str, Any]],
) -> List[Tuple[float, str]]:
    """Find monetary observations belonging to an explicit TOTAL label."""
    results: List[Tuple[float, str]] = []
    tx = total_block["x_end"]
    ty = total_block["y_center"]

    for block in candidates:
        if block is total_block:
            continue
        amounts = parse_amounts(block["text"])
        if not amounts:
            continue
        vertical = abs(block["y_center"] - ty)
        horizontal = max(0.0, block["x_start"] - tx)
        if vertical <= 90.0 and horizontal <= 2200.0:
            distance = vertical + 0.05 * horizontal
            for amount in amounts:
                results.append((distance, amount))

    return sorted(results, key=lambda item: item[0])


def extract_tax_components(
    row: Dict[str, Any],
    net_amount: Optional[float],
    page_blocks: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Extract tax components and spatially pair labels with amounts."""
    source_blocks = list(row["blocks"])
    for block in nearby_page_blocks(row, page_blocks):
        upper = block["text"].upper()
        if any(t in upper for t in ("IGST", "CGST", "SGST", "UTGST")):
            source_blocks.append(block)

    # Monetary observations in the local row band can be paired with tax labels
    # even when OCR/layout split them into separate blocks.
    local_blocks = list(row["blocks"])
    local_blocks.extend(nearby_page_blocks(row, page_blocks))

    components: List[Dict[str, Any]] = []
    for block in source_blocks:
        text = block["text"]
        upper = text.upper()
        tax_types = [
            name for name in ["IGST", "CGST", "SGST", "UTGST"]
            if name in upper
        ]
        if not tax_types:
            continue

        rates = parse_percentages(text)
        amounts = parse_amounts(text)
        neighbor_amounts = tax_amount_neighbors(block, local_blocks)
        if not amounts and neighbor_amounts:
            # Keep only the closest one. Multiple nearby monetary values can
            # belong to different columns/roles.
            amounts = [neighbor_amounts[0][1]]

        for tax_type in tax_types:
            components.append({
                "type": tax_type,
                "rate": rates[0] if rates else None,
                "amounts": amounts,
                "block": block,
            })

    # Deduplicate components by tax type + selected amount + nearest location.
    deduped: List[Dict[str, Any]] = []
    seen = set()
    for component in components:
        key = (
            component["type"],
            component["rate"],
            tuple(component["amounts"]),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(component)
    components = deduped

    explicit_rates: List[float] = []
    for block in local_blocks:
        explicit_rates.extend(parse_percentages(block["text"]))

    for component in components:
        selected = None
        if component["rate"] is not None and net_amount is not None:
            expected = net_amount * component["rate"] / 100.0
            for amount in component["amounts"]:
                if approximately_equal(float(amount), expected):
                    selected = float(amount)
                    break

        if selected is None and component["amounts"] and net_amount is not None:
            for amount in component["amounts"]:
                numeric = float(amount)
                if numeric > 0 and net_amount > 0:
                    observed_rate = numeric * 100.0 / net_amount
                    if any(abs(r - observed_rate) <= 0.15 for r in explicit_rates):
                        selected = numeric
                        if component["rate"] is None:
                            component["rate"] = observed_rate
                        break

        if selected is None and len(component["amounts"]) == 1:
            selected = float(component["amounts"][0])

        component["selected_amount"] = selected

    return components


def find_monetary_roles(
    row: Dict[str, Any],
    quantity: Optional[str],
    discount: Optional[str],
    page_blocks: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Determine unit/net/total using observed same-page monetary roles."""
    source_blocks = list(row["blocks"])

    nearby_blocks = nearby_page_blocks(row, page_blocks)
    for block in nearby_blocks:
        if parse_amounts(block["text"]) or "TOTAL" in block["text"].upper():
            source_blocks.append(block)

    amounts: List[str] = []
    for block in source_blocks:
        for amount in parse_amounts(block["text"]):
            if amount not in amounts:
                amounts.append(amount)

    if not amounts:
        return None, None, None

    # The first amount from the actual row remains the primary unit/net
    # observation; page-level additions are used for tax/total completion.
    row_amounts: List[str] = []
    for block in row["blocks"]:
        for amount in parse_amounts(block["text"]):
            if amount not in row_amounts:
                row_amounts.append(amount)
    unit_price = row_amounts[0] if row_amounts else amounts[0]

    discount_value = float(discount) if discount else 0.0
    try:
        net_value = float(unit_price) - discount_value
    except ValueError:
        net_value = None

    components = extract_tax_components(row, net_value, page_blocks)
    tax_values = [
        c["selected_amount"]
        for c in components
        if c.get("selected_amount") is not None
    ]

    total_amount = None
    if net_value is not None and tax_values:
        expected = net_value + sum(tax_values)
        for amount in amounts:
            if approximately_equal(float(amount), expected):
                total_amount = amount
        
    # Explicit TOTAL observations should win when present and are spatially
    # associated with this row. This is especially important for charge rows.
    total_candidates = []
    total_labels = []
    for block in source_blocks:
        if "TOTAL" not in block["text"].upper():
            continue
        block_amounts = parse_amounts(block["text"])
        if block_amounts:
            total_candidates.extend(block_amounts)
        else:
            total_labels.append(block)

    # Layouts frequently separate the TOTAL label from the monetary value.
    # Pair the label with nearby right-side monetary observations.
    for label in total_labels:
        for _, amount in total_amount_neighbors(label, source_blocks):
            total_candidates.append(amount)

    # De-duplicate while preserving source order.
    total_candidates = list(dict.fromkeys(total_candidates))
    if total_candidates:
        if net_value is not None and tax_values:
            expected = net_value + sum(tax_values)
            matching = [a for a in total_candidates if approximately_equal(float(a), expected)]
            if matching:
                total_amount = matching[-1]
            else:
                # Preserve an explicitly observed TOTAL rather than inventing
                # one when the arithmetic cannot be reconciled.
                total_amount = total_candidates[-1]
        elif len(total_candidates) == 1:
            total_amount = total_candidates[0]
        else:
            total_amount = total_candidates[-1]

    return unit_price, (
        f"{net_value:.2f}" if net_value is not None else None
    ), total_amount

def extract_row(row: Dict[str, Any], page_blocks: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    description = extract_description(row)

    # Some layout classifiers place wrapped description observations outside
    # PRODUCT_TABLE. Recover only same-row text, never arbitrary page text.
    if page_blocks:
        continuation = []
        row_left = min(b["x_start"] for b in row["blocks"])
        row_right = max(b["x_end"] for b in row["blocks"])
        row_width = max(row_right - row_left, 1.0)
        for block in nearby_page_blocks(row, page_blocks):
            value = block["text"].strip()
            upper = value.upper()
            if not re.search(r"[A-Za-z]{3,}", value):
                continue
            if is_header_text(value):
                continue
            if looks_like_amount_in_words(value):
                continue
            if any(token in upper for token in (
                "CGST", "SGST", "IGST", "UTGST", "HSN", "ASIN",
                "TOTAL", "AMOUNT", "QUANTITY", "PRICE", "SHIPPING CHARGES",
                "UNIT DESCRIPTION", "UNIT PRICE", "NET AMOUNT",
            )):
                continue
            if parse_hsn(value):
                continue
            # Continuation text should remain in the description area. This
            # prevents right-side tax/amount columns and column headers from
            # becoming prose.
            if block["x_start"] > row_left + row_width * 0.55:
                continue
            # A description continuation can contain an inline product code.
            # Preserve the human-readable text while removing only the code.
            if parse_asin(value):
                value = re.sub(r"\bB[0-9A-Z]{9}\b", " ", value, flags=re.I)
                value = re.sub(r"\(\s*\)\s*", " ", value)
                value = re.sub(r"\s+", " ", value).strip(" |")
                if not re.search(r"[A-Za-z]{3,}", value):
                    continue
            continuation.append({**block, "text": value})

        if continuation:
            continuation.sort(key=lambda b: (b["y_center"], b["x_start"]))
            extra_parts = [clean_description(b["text"]) for b in continuation]
            extra = " ".join(part for part in extra_parts if part)
            if extra.strip():
                description = clean_description(" ".join(filter(None, [description, extra])))

    text = row_text(row)

    # Classify charges generically from their observed description.
    description_lower = (description or "").lower()
    is_shipping = "shipping charge" in description_lower
    is_fee = any(term in description_lower for term in [
        "fee", "charge", "service charge", "service fee",
    ])

    item_type = "charge" if (is_shipping or is_fee) else "product"
    charge_type = None

    if is_shipping:
        charge_type = "Shipping"
    elif "cash" in description_lower and "delivery" in description_lower:
        charge_type = "COD Fee"
    elif is_fee:
        charge_type = "Fee"

    quantity = extract_quantity(row)
    discount = explicit_discount(row)

    unit_price, net_amount, total_amount = find_monetary_roles(
        row, quantity, discount, page_blocks
    )

    net_value = float(net_amount) if net_amount else None
    tax_components = extract_tax_components(row, net_value, page_blocks)

    tax_rate_values = [
        c["rate"] for c in tax_components if c.get("rate") is not None
    ]
    tax_type_values = []
    for component in tax_components:
        if component["type"] not in tax_type_values:
            tax_type_values.append(component["type"])

    selected_tax_values = [
        c["selected_amount"]
        for c in tax_components
        if c.get("selected_amount") is not None
    ]

    tax_amount = (
        f"{sum(selected_tax_values):.2f}"
        if selected_tax_values
        else None
    )

    # Re-check total after tax resolution.
    if net_value is not None and tax_amount is not None:
        expected_total = net_value + float(tax_amount)
        row_amounts = []
        for block in row["blocks"]:
            row_amounts.extend(parse_amounts(block["text"]))

        matches = [
            value for value in row_amounts
            if approximately_equal(float(value), expected_total)
        ]
        if matches:
            total_amount = matches[-1]

    # Product identifiers are strictly page/row scoped.
    asin = None
    hsn = None

    identifier_blocks = list(row["blocks"])
    if page_blocks:
        row_center_y = sum(b["y_center"] for b in row["blocks"]) / len(row["blocks"])
        row_center_x = sum(b["x_center"] for b in row["blocks"]) / len(row["blocks"])
        extras = []
        for block in page_blocks:
            if block in identifier_blocks:
                continue
            if parse_asin(block["text"]) or parse_hsn(block["text"]):
                distance = abs(block["y_center"] - row_center_y) + 0.15 * abs(block["x_center"] - row_center_x)
                extras.append((distance, block))
        extras.sort(key=lambda item: item[0])
        identifier_blocks.extend(block for _, block in extras)

    for block in identifier_blocks:
        if asin is None:
            asin = parse_asin(block["text"])
        if hsn is None:
            hsn = parse_hsn(block["text"])
        if asin and hsn:
            break

    return {
        "item_type": item_type,
        "charge_type": charge_type,
        "description": description,
        "quantity": quantity,
        "unit_price": unit_price,
        "discount": discount,
        "net_amount": net_amount,
        "tax_rate": (
            "/".join(f"{value:g}%" for value in dict.fromkeys(tax_rate_values))
            if tax_rate_values else None
        ),
        "tax_type": "/".join(tax_type_values) if tax_type_values else None,
        "tax_amount": tax_amount,
        "total_amount": total_amount,
        "asin": asin,
        "hsn": hsn,
    }


# ---------------------------------------------------------------------------
# Validation and output
# ---------------------------------------------------------------------------

def validate_row(item: Dict[str, Any]) -> List[str]:
    errors = []

    if not item["description"]:
        errors.append("Description not detected.")

    if item["item_type"] == "product" and not item["quantity"]:
        errors.append("Quantity not detected.")

    if not item["unit_price"]:
        errors.append("Amount/unit price not detected.")

    if item["tax_rate"] and item["tax_amount"] and item["total_amount"]:
        try:
            # This validation is intentionally skipped for multiple tax
            # components whose rate is represented as "2.5%/2.5%".
            rates = [
                float(value)
                for value in re.findall(r"\d+(?:\.\d+)?", item["tax_rate"])
            ]
            tax_types = [part for part in (item["tax_type"] or "").split("/") if part]
            # If CGST and SGST are both observed but the same rate was
            # represented once, the effective rate is the sum of both
            # components. This is derived from observations, not a standard
            # tax-rate lookup.
            if len(rates) == 1 and len(tax_types) > 1:
                rates = rates * len(tax_types)

            net = float(item["net_amount"]) if item["net_amount"] else None
            tax = float(item["tax_amount"])
            total = float(item["total_amount"])

            if net is not None and rates:
                expected_tax = net * sum(rates) / 100.0
                if not approximately_equal(expected_tax, tax):
                    errors.append(
                        f"Tax arithmetic mismatch: expected {expected_tax:.2f}, "
                        f"got {tax:.2f}"
                    )

                expected_total = net + tax
                if not approximately_equal(expected_total, total):
                    errors.append(
                        f"Total calculation mismatch: expected "
                        f"{expected_total:.2f}, got {total:.2f}"
                    )
        except (TypeError, ValueError):
            errors.append("Could not validate monetary arithmetic.")

    return errors


def process_page(page: str, blocks: List[Dict[str, Any]], all_page_blocks: Optional[List[Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    rows = build_rows(blocks)
    extracted: List[Dict[str, Any]] = []

    print(f"\n{'-' * 70}")
    print(f"PROCESSING PAGE {page}")
    print(f"OCR blocks in PRODUCT_TABLE: {len(blocks)}")
    print(f"Detected candidate rows: {len(rows)}")

    for index, row in enumerate(rows, start=1):
        item = extract_row(row, all_page_blocks)
        item["page_number"] = page
        item["row_number"] = index

        errors = validate_row(item)
        item["validation_status"] = "PASS" if not errors else "REVIEW"
        item["validation_message"] = (
            "; ".join(errors) if errors else ""
        )

        extracted.append(item)

        print(f"\nROW {index}")
        print(f"  Type         : {item['item_type']}")
        print(f"  Charge type  : {item['charge_type']}")
        print(f"  Description  : {item['description']}")
        print(f"  Quantity     : {item['quantity']}")
        print(f"  Unit/Amount  : {item['unit_price']}")
        print(f"  Net Amount   : {item['net_amount']}")
        print(f"  Tax Rate     : {item['tax_rate']}")
        print(f"  Tax Type     : {item['tax_type']}")
        print(f"  Tax Amount   : {item['tax_amount']}")
        print(f"  Total        : {item['total_amount']}")
        print(f"  ASIN         : {item['asin']}")
        print(f"  HSN          : {item['hsn']}")
        print(f"  Validation   : {item['validation_status']}")

    return extracted


def main() -> None:
    print("=" * 70)
    print("UNIVERSAL PRODUCT / CHARGE TABLE EXTRACTION")
    print("=" * 70)
    print(f"\nWorking directory: {WORK_DIR}")
    print(f"Spatial regions:   {SPATIAL_REGIONS_FILE}")
    print(f"Output file:       {OUTPUT_FILE}")

    blocks, page_column, all_blocks = load_blocks()

    if not blocks:
        raise ValueError("No PRODUCT_TABLE region found.")

    print(f"\nFound {len(blocks)} OCR blocks in PRODUCT_TABLE region.")
    print(f"Page column: {page_column or 'not present (single page assumption)'}")

    all_items: List[Dict[str, Any]] = []

    all_blocks_by_page = {
        page: page_blocks
        for page, page_blocks in group_by_page(all_blocks)
    }

    for page, page_blocks in group_by_page(blocks):
        all_items.extend(
            process_page(page, page_blocks, all_blocks_by_page.get(str(page), []))
        )

    if not all_items:
        raise ValueError("No product/charge rows could be reconstructed.")

    fieldnames = [
        "page_number",
        "row_number",
        "item_type",
        "charge_type",
        "description",
        "quantity",
        "unit_price",
        "discount",
        "net_amount",
        "tax_rate",
        "tax_type",
        "tax_amount",
        "total_amount",
        "asin",
        "hsn",
        "validation_status",
        "validation_message",
    ]

    with OUTPUT_FILE.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_items)

    passed = sum(
        1 for item in all_items
        if item["validation_status"] == "PASS"
    )

    print("\n" + "=" * 70)
    print("TABLE EXTRACTION COMPLETE")
    print("=" * 70)
    print(f"Rows extracted : {len(all_items)}")
    print(f"Rows passed    : {passed}")
    print(f"Rows review    : {len(all_items) - passed}")
    print(f"Saved CSV      : {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
