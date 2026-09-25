import os
import re
import sys
from typing import Dict, List, Optional

import pandas as pd


# ============================================================
# UNIVERSAL SPATIAL PARSER
# ============================================================
#
# Usage:
#   python src\spatial_parser.py output\test_document
#
# The parser consumes layout-analysis blocks and assigns a
# semantic spatial_region to each OCR block.
#
# Design principles:
# - Page-aware: never mixes blocks from different pages.
# - Observation-first: uses detected section labels, geometry,
#   and document structure rather than fixed invoice coordinates.
# - Generic: does not depend on one company, seller, product,
#   invoice number, or fixed document coordinates.
# - Backward compatible: falls back to the original page-1
#   layout-analysis CSV if the all-pages CSV is unavailable.
# ============================================================


DEFAULT_WORK_DIR = "output"

ALL_PAGES_INPUT = "invoice_blocks_with_sections_all_pages.csv"
LEGACY_INPUT = "invoice_blocks_with_sections.csv"
OUTPUT_FILE = "spatial_regions.csv"

# This is only a relative page-layout heuristic. It is not a
# document-specific coordinate and is calculated separately for
# every page.
RIGHT_COLUMN_RATIO = 0.55


# ============================================================
# TEXT HELPERS
# ============================================================

def normalize_text(text) -> str:
    if pd.isna(text):
        return ""

    text = str(text)
    text = text.replace("\n", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def compact_text(text) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize_text(text).lower())


def has_pattern(text, pattern: str) -> bool:
    return re.search(
        pattern,
        normalize_text(text),
        re.IGNORECASE,
    ) is not None


def section_value(row) -> str:
    return normalize_text(row.get("section", "")).upper()


def first_index(df: pd.DataFrame, predicate) -> Optional[int]:
    for idx, row in df.iterrows():
        if predicate(row):
            return idx
    return None


def all_indices(df: pd.DataFrame, predicate) -> List[int]:
    result = []
    for idx, row in df.iterrows():
        if predicate(row):
            result.append(idx)
    return result


# ============================================================
# PAGE DETECTION
# ============================================================

def detect_page_column(df: pd.DataFrame) -> Optional[str]:
    candidates = [
        "page_number",
        "page",
        "page_num",
        "page_no",
        "page_index",
    ]

    for column in candidates:
        if column in df.columns:
            return column

    return None


def prepare_page_column(df: pd.DataFrame) -> pd.DataFrame:
    page_column = detect_page_column(df)

    if page_column is not None:
        values = pd.to_numeric(df[page_column], errors="coerce")
        if values.notna().any():
            df["__page_number"] = values.fillna(1).astype(int)
        else:
            df["__page_number"] = 1
        return df

    # If the all-pages file has no page column, keep it as one
    # logical page rather than guessing from coordinates.
    df["__page_number"] = 1
    return df


# ============================================================
# INPUT
# ============================================================

def resolve_input_file(work_dir: str) -> str:
    all_pages = os.path.join(work_dir, ALL_PAGES_INPUT)
    legacy = os.path.join(work_dir, LEGACY_INPUT)

    if os.path.exists(all_pages):
        return all_pages

    return legacy


def load_input(work_dir: str):
    input_file = resolve_input_file(work_dir)

    if not os.path.exists(input_file):
        raise FileNotFoundError(
            "No layout-analysis input found.\n"
            f"Expected either:\n"
            f"  {os.path.abspath(os.path.join(work_dir, ALL_PAGES_INPUT))}\n"
            f"  {os.path.abspath(os.path.join(work_dir, LEGACY_INPUT))}"
        )

    df = pd.read_csv(input_file)

    required = [
        "block_number",
        "text",
        "x_start",
        "x_end",
        "y_start",
        "y_end",
    ]

    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"Missing required columns: {missing}"
        )

    if "section" not in df.columns:
        df["section"] = ""

    df["text"] = df["text"].apply(normalize_text)

    numeric_columns = [
        "block_number",
        "x_start",
        "x_end",
        "y_start",
        "y_end",
    ]

    for column in numeric_columns:
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        )

    df = df.dropna(subset=numeric_columns).copy()
    df = prepare_page_column(df)

    # Preserve source ordering where possible. Within each page,
    # spatial reasoning is easier if blocks are ordered top-to-bottom.
    df = df.sort_values(
        by=["__page_number", "y_start", "x_start", "block_number"]
    ).reset_index(drop=True)

    return df, input_file


# ============================================================
# ANCHOR DETECTION
# ============================================================

def find_anchor(df: pd.DataFrame, section: str, patterns=None):
    section = section.upper()

    # Prefer the semantic section produced by layout_analysis.py.
    matches = df.index[
        df["section"].astype(str).str.upper().str.strip() == section
    ].tolist()

    if matches:
        return matches[0]

    if patterns:
        return first_index(
            df,
            lambda row: any(
                has_pattern(row["text"], pattern)
                for pattern in patterns
            ),
        )

    return None


def find_all_section(df: pd.DataFrame, section: str) -> List[int]:
    section = section.upper()
    return df.index[
        df["section"].astype(str).str.upper().str.strip() == section
    ].tolist()


# ============================================================
# REGION ASSIGNMENT
# ============================================================

REGIONS = [
    "SELLER",
    "SELLER_TAX",
    "BILLING",
    "SHIPPING",
    "SUPPLY_DELIVERY",
    "ORDER",
    "INVOICE_DETAILS",
    "PRODUCT_TABLE",
    "TOTAL",
    "AMOUNT_IN_WORDS",
    "SIGNATORY",
    "REVERSE_CHARGE",
    "PAYMENT",
]


def empty_regions() -> Dict[str, List[int]]:
    return {name: [] for name in REGIONS}


def add_region(regions, region: str, block_number):
    if region in regions:
        regions[region].append(int(block_number))


def monetary_values(text: str) -> List[float]:
    """
    Generic monetary-number detector.

    It intentionally does not depend on a particular currency symbol.
    """
    text = normalize_text(text)

    matches = re.findall(
        r"(?<![A-Za-z0-9])"
        r"(?:₹|Rs\.?|INR|\$|€|£)?\s*"
        r"-?\d[\d,]*"
        r"(?:\.\d{1,2})?"
        r"(?![A-Za-z0-9])",
        text,
        flags=re.IGNORECASE,
    )

    values = []

    for value in matches:
        cleaned = re.sub(r"[^0-9.\-]", "", value)

        if cleaned in {"", "-", "."}:
            continue

        try:
            number = float(cleaned)
            values.append(number)
        except ValueError:
            pass

    return values


def likely_right_side(row, split_x: float) -> bool:
    return (
        float(row["x_start"]) >= split_x
        or float(row["x_end"]) >= split_x
    )


def assign_page_regions(page_df: pd.DataFrame) -> Dict[str, List[int]]:
    """
    Assign blocks from one page only.

    Important: every page gets its own anchors. This prevents a
    multi-page document from using page 1's seller/order/total
    boundaries for page 2.
    """
    df = page_df.copy()

    regions = empty_regions()

    if df.empty:
        return regions

    document_width = float(df["x_end"].max())
    split_x = document_width * RIGHT_COLUMN_RATIO

    # --------------------------------------------------------
    # Semantic anchors
    # --------------------------------------------------------

    seller_heading = find_anchor(
        df,
        "SELLER",
        [r"^Sold\s+By\s*:?\s*$"],
    )

    billing_heading = find_anchor(
        df,
        "BILLING",
        [r"^Billing\s+Address\s*:?\s*$"],
    )

    shipping_heading = find_anchor(
        df,
        "SHIPPING",
        [r"^Shipping\s+Address\s*:?\s*$"],
    )

    seller_tax_anchors = find_all_section(df, "SELLER_TAX")

    supply_heading = find_anchor(
        df,
        "SUPPLY_DELIVERY",
        [
            r"Place\s+of\s+supply",
            r"Place\s+of\s+delivery",
        ],
    )

    order_anchors = find_all_section(df, "ORDER")

    invoice_anchors = find_all_section(
        df,
        "INVOICE_DETAILS",
    )

    product_anchors = find_all_section(
        df,
        "PRODUCT_TABLE",
    )

    total_anchors = find_all_section(df, "TOTAL")

    amount_words_heading = find_anchor(
        df,
        "AMOUNT_IN_WORDS",
        [
            r"Amount\s+in\s+Words",
            r"Amoun.*Words",
            r"in\s+Words",
        ],
    )

    # A signatory can begin with "For <entity>:" and only then show
    # "Authorized Signatory".  Treat both as one region and choose the
    # earliest observed line so the "For ..." line is not classified as
    # amount-in-words.
    signatory_candidates = find_all_section(df, "SIGNATORY")

    for idx, row in df.iterrows():
        if re.match(
            r"^For\s+.+:\s*$",
            normalize_text(row["text"]),
            flags=re.IGNORECASE,
        ):
            signatory_candidates.append(idx)

    for idx, row in df.iterrows():
        if re.search(
            r"^Authorized\s+Signatory\b",
            normalize_text(row["text"]),
            flags=re.IGNORECASE,
        ):
            signatory_candidates.append(idx)

    signatory_heading = (
        min(
            signatory_candidates,
            key=lambda idx: float(df.loc[idx, "y_start"]),
        )
        if signatory_candidates
        else None
    )

    reverse_heading = find_anchor(
        df,
        "TAX_PAYMENT",
        [r"reverse\s+charge"],
    )

    payment_heading = find_anchor(
        df,
        "PAYMENT",
        [r"Payment\s+Transaction\s+ID"],
    )

    # --------------------------------------------------------
    # Anchor coordinates
    # --------------------------------------------------------

    def y(idx):
        if idx is None:
            return None
        return float(df.loc[idx, "y_start"])

    seller_y = y(seller_heading)
    billing_y = y(billing_heading)
    shipping_y = y(shipping_heading)
    supply_y = y(supply_heading)
    amount_y = y(amount_words_heading)
    signatory_y = y(signatory_heading)
    reverse_y = y(reverse_heading)
    payment_y = y(payment_heading)

    # For order/invoice/product/total, use all detected anchors
    # instead of assuming there is only one occurrence.
    order_ys = sorted(
        [y(i) for i in order_anchors if y(i) is not None]
    )

    invoice_ys = sorted(
        [y(i) for i in invoice_anchors if y(i) is not None]
    )

    product_y = min(
        [y(i) for i in product_anchors if y(i) is not None],
        default=None,
    )

    total_y = min(
        [y(i) for i in total_anchors if y(i) is not None],
        default=None,
    )

    # --------------------------------------------------------
    # Helper for nearest section boundary
    # --------------------------------------------------------

    def nearest_y_above(values, current_y):
        values = [
            value for value in values
            if value is not None and value <= current_y
        ]
        return max(values) if values else None

    # --------------------------------------------------------
    # First pass: semantic/structural regions
    # --------------------------------------------------------

    assigned = {}

    for _, row in df.iterrows():
        block = int(row["block_number"])
        text = normalize_text(row["text"])
        x_start = float(row["x_start"])
        y_start = float(row["y_start"])

        region = None

        # Preserve explicit semantic labels first.
        section = section_value(row)

        # These sections are already high-confidence structural
        # observations from layout_analysis.py.
        if section == "SELLER_TAX":
            region = "SELLER_TAX"

        elif section == "SUPPLY_DELIVERY":
            region = "SUPPLY_DELIVERY"

        elif section == "PAYMENT":
            region = "PAYMENT"

        elif section == "TAX_PAYMENT":
            region = "REVERSE_CHARGE"

        elif section == "SIGNATORY":
            region = "SIGNATORY"

        elif section == "AMOUNT_IN_WORDS":
            region = "AMOUNT_IN_WORDS"

        elif section == "TOTAL":
            # Header rows containing "Net Tax..." can be part of the
            # table header. Keep explicit TOTAL labels/summary blocks
            # in TOTAL; ordinary table-header text will be handled below.
            if has_pattern(text, r"\bTOTAL\b") or monetary_values(text):
                region = "TOTAL"

        # ----------------------------------------------------
        # Seller left column
        # ----------------------------------------------------
        if region is None and seller_y is not None:
            if x_start < split_x and y_start >= seller_y:
                if billing_y is not None:
                    # Seller content normally ends before the seller
                    # tax block, not at the right-side billing heading.
                    seller_tax_y = min(
                        [y(i) for i in seller_tax_anchors if y(i) is not None],
                        default=None,
                    )

                    if seller_tax_y is not None:
                        if y_start < seller_tax_y:
                            region = "SELLER"
                    else:
                        if supply_y is None or y_start < supply_y:
                            region = "SELLER"

        # ----------------------------------------------------
        # Seller tax
        # ----------------------------------------------------
        if region is None and seller_tax_anchors:
            seller_tax_y = min(
                [y(i) for i in seller_tax_anchors if y(i) is not None],
                default=None,
            )

            if seller_tax_y is not None:
                if (
                    x_start < split_x
                    and y_start >= seller_tax_y
                    and (supply_y is None or y_start < supply_y)
                ):
                    # PAN/GST and closely associated seller tax data.
                    region = "SELLER_TAX"

        # ----------------------------------------------------
        # Billing right column
        # ----------------------------------------------------
        if region is None and billing_y is not None:
            if x_start >= split_x and y_start >= billing_y:
                if shipping_y is None or y_start < shipping_y:
                    region = "BILLING"

        # ----------------------------------------------------
        # Shipping right column
        # ----------------------------------------------------
        if region is None and shipping_y is not None:
            if x_start >= split_x and y_start >= shipping_y:
                if supply_y is None or y_start < supply_y:
                    region = "SHIPPING"

        # ----------------------------------------------------
        # Supply / delivery
        # ----------------------------------------------------
        if region is None and supply_y is not None:
            if y_start >= supply_y:
                first_order_y = min(order_ys) if order_ys else None

                if first_order_y is None or y_start < first_order_y:
                    if x_start >= split_x:
                        region = "SUPPLY_DELIVERY"

        # ----------------------------------------------------
        # Order
        # ----------------------------------------------------
        if region is None and order_ys:
            first_order_y = min(order_ys)
            first_invoice_y = min(invoice_ys) if invoice_ys else None
            first_product_y = product_y

            if y_start >= first_order_y:
                if first_product_y is None or y_start < first_product_y:
                    if x_start < split_x and (
                        has_pattern(text, r"\bOrder\s+(Number|Date)\b")
                        or section == "ORDER"
                    ):
                        region = "ORDER"

        # ----------------------------------------------------
        # Invoice details
        # ----------------------------------------------------
        if region is None and invoice_ys:
            first_invoice_y = min(invoice_ys)

            if y_start >= first_invoice_y:
                if product_y is None or y_start < product_y:
                    if x_start >= split_x and (
                        has_pattern(
                            text,
                            r"\bInvoice\s+(Number|Date|Details)\b",
                        )
                        or section == "INVOICE_DETAILS"
                    ):
                        region = "INVOICE_DETAILS"

        # ----------------------------------------------------
        # Product table
        # ----------------------------------------------------
        if region is None and product_y is not None:
            if y_start >= product_y:
                if total_y is not None:
                    if y_start < total_y:
                        region = "PRODUCT_TABLE"
                elif amount_y is not None and y_start < amount_y:
                    region = "PRODUCT_TABLE"

        # ----------------------------------------------------
        # Amount in words
        # ----------------------------------------------------
        if region is None and amount_y is not None:
            if y_start >= amount_y:
                end_y = min(
                    [
                        value
                        for value in [
                            signatory_y,
                            reverse_y,
                            payment_y,
                        ]
                        if value is not None
                    ],
                    default=float("inf"),
                )

                if y_start < end_y:
                    region = "AMOUNT_IN_WORDS"

        # ----------------------------------------------------
        # Signatory
        # ----------------------------------------------------
        if region is None and signatory_y is not None:
            if y_start >= signatory_y:
                end_y = min(
                    [
                        value
                        for value in [
                            reverse_y,
                            payment_y,
                        ]
                        if value is not None
                    ],
                    default=float("inf"),
                )

                if y_start < end_y:
                    region = "SIGNATORY"

        # ----------------------------------------------------
        # Reverse charge
        # ----------------------------------------------------
        if region is None and reverse_y is not None:
            if y_start >= reverse_y:
                end_y = payment_y or float("inf")
                if y_start < end_y:
                    region = "REVERSE_CHARGE"

        # ----------------------------------------------------
        # Payment
        # ----------------------------------------------------
        if region is None and payment_y is not None:
            if y_start >= payment_y:
                region = "PAYMENT"

        if region is not None:
            assigned[block] = region
            add_region(regions, region, block)

    # --------------------------------------------------------
    # Semantic fallback for important sections
    # --------------------------------------------------------

    # A block explicitly marked by layout analysis should not be
    # lost just because a page uses an unusual column arrangement.
    fallback_sections = {
        "ORDER": "ORDER",
        "INVOICE_DETAILS": "INVOICE_DETAILS",
        "PRODUCT_TABLE": "PRODUCT_TABLE",
        "TOTAL": "TOTAL",
        "AMOUNT_IN_WORDS": "AMOUNT_IN_WORDS",
        "PAYMENT": "PAYMENT",
        "SELLER_TAX": "SELLER_TAX",
        "SUPPLY_DELIVERY": "SUPPLY_DELIVERY",
        "SIGNATORY": "SIGNATORY",
    }

    for section_name, region_name in fallback_sections.items():
        semantic = find_all_section(df, section_name)

        for idx in semantic:
            block = int(df.loc[idx, "block_number"])

            if block not in assigned:
                assigned[block] = region_name
                add_region(regions, region_name, block)

    # --------------------------------------------------------
    # Final monetary summary immediately above TOTAL
    # --------------------------------------------------------
    #
    # Some invoice layouts place a compact row such as
    # "tax amount | grand total" directly above the TOTAL label.
    # Because it is visually adjacent to the product table, the first
    # table pass can otherwise classify it as PRODUCT_TABLE.  Move only
    # the nearest right-side row with multiple monetary observations.
    if total_y is not None:
        candidates = []

        for _, row in df.iterrows():
            block = int(row["block_number"])

            if block in assigned:
                continue

            row_y = float(row["y_start"])

            if row_y >= total_y:
                continue

            if not likely_right_side(row, split_x):
                continue

            if len(monetary_values(row["text"])) < 2:
                continue

            candidates.append((row_y, block))

        if candidates:
            _, nearest_block = max(candidates, key=lambda item: item[0])
            assigned[nearest_block] = "TOTAL"
            add_region(regions, "TOTAL", nearest_block)

    # Generic product-table fallback
    # --------------------------------------------------------
    #
    # If layout analysis found a PRODUCT_TABLE section but only
    # headers were marked, collect unassigned blocks between the
    # table start and the total/amount boundary. This preserves
    # product observations for downstream extraction.
    # --------------------------------------------------------

    product_start_y = product_y

    if product_start_y is None:
        product_section_indices = find_all_section(df, "PRODUCT_TABLE")
        if product_section_indices:
            product_start_y = min(
                float(df.loc[i, "y_start"])
                for i in product_section_indices
            )

    if product_start_y is not None:
        end_candidates = [
            value
            for value in [total_y, amount_y]
            if value is not None and value > product_start_y
        ]

        product_end_y = min(end_candidates) if end_candidates else None

        for _, row in df.iterrows():
            block = int(row["block_number"])

            if block in assigned:
                continue

            row_y = float(row["y_start"])

            if row_y < product_start_y:
                continue

            if product_end_y is not None and row_y >= product_end_y:
                continue

            # Do not pull clearly unrelated lower-page content into
            # the table.
            if section_value(row) in {
                "PAYMENT",
                "SIGNATORY",
                "TAX_PAYMENT",
                "AMOUNT_IN_WORDS",
            }:
                continue

            if (
                section_value(row) == "PRODUCT_TABLE"
                or monetary_values(row["text"])
                or has_pattern(
                    row["text"],
                    r"\b(HSN|SKU|ASIN|Qty|Quantity|Description)\b",
                )
            ):
                assigned[block] = "PRODUCT_TABLE"
                add_region(
                    regions,
                    "PRODUCT_TABLE",
                    block,
                )

    # --------------------------------------------------------
    # Total fallback
    # --------------------------------------------------------
    #
    # If the TOTAL label was detected but its numeric summary was
    # not assigned, use right-side monetary blocks immediately
    # before Amount in Words. This is a generic structural fallback.
    # --------------------------------------------------------

    if amount_y is not None:
        for _, row in df.iterrows():
            block = int(row["block_number"])

            if block in assigned:
                continue

            row_y = float(row["y_start"])

            if row_y >= amount_y:
                continue

            if total_y is not None and row_y < total_y:
                continue

            if not likely_right_side(row, split_x):
                continue

            values = monetary_values(row["text"])

            if len(values) >= 1:
                assigned[block] = "TOTAL"
                add_region(
                    regions,
                    "TOTAL",
                    block,
                )

    # --------------------------------------------------------
    # De-duplicate and preserve source order
    # --------------------------------------------------------

    for region_name in regions:
        regions[region_name] = list(
            dict.fromkeys(regions[region_name])
        )

    return regions


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 80)
    print("UNIVERSAL SPATIAL PARSER")
    print("=" * 80)
    print()

    if len(sys.argv) > 1 and sys.argv[1].strip():
        work_dir = sys.argv[1].strip()
    else:
        work_dir = DEFAULT_WORK_DIR

    os.makedirs(work_dir, exist_ok=True)

    print(
        f"Working directory : {os.path.abspath(work_dir)}"
    )

    try:
        df, input_file = load_input(work_dir)
    except Exception as exc:
        print()
        print("ERROR:", exc)
        return

    output_file = os.path.join(
        work_dir,
        OUTPUT_FILE,
    )

    print(
        f"Input file        : {os.path.abspath(input_file)}"
    )
    print(
        f"Output file       : {os.path.abspath(output_file)}"
    )
    print(
        f"Loaded blocks     : {len(df)}"
    )

    page_numbers = sorted(
        df["__page_number"].unique().tolist()
    )

    print(
        f"Detected pages    : {len(page_numbers)}"
    )
    print()

    all_output = []
    overall_counts = {region: 0 for region in REGIONS}

    for page_number in page_numbers:
        page_df = df[
            df["__page_number"] == page_number
        ].copy()

        print("=" * 80)
        print(f"PROCESSING PAGE {page_number}")
        print("=" * 80)
        print(
            f"Blocks on page   : {len(page_df)}"
        )

        regions = assign_page_regions(page_df)

        lookup = {}

        for region_name, blocks in regions.items():
            for block in blocks:
                lookup[block] = region_name
                overall_counts[region_name] += 1

        page_output = page_df.copy()

        page_output["spatial_region"] = (
            page_output["block_number"]
            .map(lookup)
            .fillna("")
        )

        all_output.append(page_output)

        print()
        print("Regions detected:")
        for region_name in REGIONS:
            count = len(regions[region_name])
            if count:
                print(
                    f"  {region_name:<20} {count} blocks"
                )

        print()

        # Print a concise block trace so the user can verify
        # important extraction boundaries without flooding the
        # terminal with every unassigned block.
        important_regions = [
            "SELLER",
            "SELLER_TAX",
            "BILLING",
            "SHIPPING",
            "SUPPLY_DELIVERY",
            "ORDER",
            "INVOICE_DETAILS",
            "PRODUCT_TABLE",
            "TOTAL",
            "AMOUNT_IN_WORDS",
            "PAYMENT",
        ]

        for region_name in important_regions:
            region_df = page_output[
                page_output["spatial_region"] == region_name
            ]

            if region_df.empty:
                continue

            print("-" * 80)
            print(f"{region_name} REGION")

            for _, row in region_df.iterrows():
                print(
                    f"BLOCK {int(row['block_number']):03d} | "
                    f"X={row['x_start']:.1f}-{row['x_end']:.1f} | "
                    f"Y={row['y_start']:.1f}-{row['y_end']:.1f} | "
                    f"{row['text']}"
                )

        print()

    if not all_output:
        print("ERROR: No page output was generated.")
        return

    output_df = pd.concat(
        all_output,
        ignore_index=True,
    )

    # Remove internal helper column from persisted output.
    output_df = output_df.drop(
        columns=["__page_number"],
        errors="ignore",
    )

    # Keep the original useful columns first, then spatial_region.
    preferred_columns = [
        "page_number",
        "page",
        "block_number",
        "text",
        "x_start",
        "x_end",
        "y_start",
        "y_end",
        "confidence",
        "section",
        "spatial_region",
    ]

    ordered_columns = [
        column
        for column in preferred_columns
        if column in output_df.columns
    ]

    remaining_columns = [
        column
        for column in output_df.columns
        if column not in ordered_columns
    ]

    output_df = output_df[
        ordered_columns + remaining_columns
    ]

    output_df.to_csv(
        output_file,
        index=False,
    )

    print("=" * 80)
    print("SPATIAL PARSING COMPLETE")
    print("=" * 80)
    print()
    print(
        f"Output file: {os.path.abspath(output_file)}"
    )
    print()
    print("Overall regions detected:")

    for region_name in REGIONS:
        count = overall_counts[region_name]
        if count:
            print(
                f"  {region_name:<20} {count} blocks"
            )

    print()
    print(
        "Page boundaries were processed independently."
    )
    print(
        "No document-specific coordinates or seller/product values were hard-coded."
    )


if __name__ == "__main__":
    main()
