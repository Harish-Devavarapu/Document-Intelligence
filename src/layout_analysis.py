import os
import re
import sys
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd


# ============================================================
# Universal layout analysis
# ============================================================
# Input:
#   <work_dir>/page_N_ocr_data.csv
#
# Output:
#   invoice_blocks.csv                         (page 1/backward compatibility)
#   invoice_blocks_with_sections.csv           (page 1/backward compatibility)
#   invoice_blocks_page_N.csv                  (each page)
#   invoice_blocks_with_sections_page_N.csv    (each page)
#   invoice_blocks_all_pages.csv               (all pages, page-aware)
#   invoice_blocks_with_sections_all_pages.csv (all pages, page-aware)
#
# The analyzer is observation-first:
# - no document/company-specific coordinates
# - no fixed invoice template
# - adaptive grouping from observed word geometry
# - preserves page boundaries
# ============================================================


DEFAULT_WORK_DIR = "output"


def get_work_dir() -> str:
    if len(sys.argv) > 1 and sys.argv[1].strip():
        return sys.argv[1].strip()
    return DEFAULT_WORK_DIR


WORK_DIR = get_work_dir()
OUTPUT_DIR = WORK_DIR


def natural_page_key(path: str) -> int:
    match = re.search(r"page_(\d+)_ocr_data\.csv$", os.path.basename(path), re.IGNORECASE)
    return int(match.group(1)) if match else 10**9


def discover_ocr_files(work_dir: str) -> List[str]:
    files = []
    if not os.path.isdir(work_dir):
        return files

    for name in os.listdir(work_dir):
        if re.fullmatch(r"page_\d+_ocr_data\.csv", name, re.IGNORECASE):
            files.append(os.path.join(work_dir, name))

    return sorted(files, key=natural_page_key)


def clean_ocr_data(raw: pd.DataFrame, page_number: int) -> pd.DataFrame:
    required = ["text", "left", "top", "width", "height"]
    missing = [c for c in required if c not in raw.columns]
    if missing:
        raise ValueError(
            f"Missing required OCR columns on page {page_number}: {', '.join(missing)}"
        )

    data = raw.copy()

    data["text"] = (
        data["text"]
        .astype(str)
        .str.replace("\u00a0", " ", regex=False)
        .str.strip()
    )
    data = data[data["text"] != ""].copy()

    numeric_columns = ["left", "top", "width", "height", "conf"]
    for column in numeric_columns:
        if column not in data.columns:
            data[column] = 0.0
        data[column] = pd.to_numeric(data[column], errors="coerce")

    data = data.dropna(subset=["left", "top", "width", "height"]).copy()

    # Ignore impossible geometry, but do not discard low-confidence text.
    data = data[
        (data["width"] > 0)
        & (data["height"] > 0)
        & (data["left"] >= 0)
        & (data["top"] >= 0)
    ].copy()

    data["conf"] = data["conf"].fillna(0.0)

    data["right"] = data["left"] + data["width"]
    data["bottom"] = data["top"] + data["height"]
    data["center_x"] = data["left"] + data["width"] / 2.0
    data["center_y"] = data["top"] + data["height"] / 2.0
    data["page_number"] = page_number

    data = data.sort_values(
        ["center_y", "left"],
        kind="mergesort",
    ).reset_index(drop=True)

    return data


def median_positive(values: Iterable[float], fallback: float) -> float:
    series = pd.Series(list(values), dtype="float64")
    series = series[series > 0]
    if series.empty:
        return fallback
    return float(series.median())


def adaptive_y_tolerance(data: pd.DataFrame) -> float:
    median_height = median_positive(data["height"], 40.0)

    # Text on the same visual row can have slightly different baselines.
    # Keep this relative to the observed document scale.
    tolerance = median_height * 0.30
    return max(6.0, min(24.0, tolerance))


def group_words_into_lines(data: pd.DataFrame) -> List[List[pd.Series]]:
    y_tolerance = adaptive_y_tolerance(data)

    lines: List[List[pd.Series]] = []
    current: List[pd.Series] = []
    current_y: Optional[float] = None

    for _, row in data.iterrows():
        word_y = float(row["center_y"])

        if current_y is None:
            current = [row]
            current_y = word_y
            continue

        if abs(word_y - current_y) <= y_tolerance:
            current.append(row)
            current_y = sum(float(w["center_y"]) for w in current) / len(current)
        else:
            if current:
                lines.append(current)
            current = [row]
            current_y = word_y

    if current:
        lines.append(current)

    return lines


def adaptive_x_gap_threshold(data: pd.DataFrame) -> float:
    median_height = median_positive(data["height"], 40.0)
    median_width = median_positive(data["width"], 60.0)

    # A block boundary should depend on the actual word scale.
    # Cap it so large pages do not turn the entire row into one block.
    threshold = max(median_height * 1.4, median_width * 1.25)
    return max(45.0, min(180.0, threshold))


def split_line_into_blocks(
    line: List[pd.Series],
    x_gap_threshold: float,
) -> List[List[pd.Series]]:
    ordered = sorted(line, key=lambda item: float(item["left"]))

    blocks: List[List[pd.Series]] = []
    current: List[pd.Series] = []
    previous_right: Optional[float] = None

    for word in ordered:
        current_left = float(word["left"])

        if previous_right is None:
            current = [word]
            previous_right = float(word["right"])
            continue

        gap = current_left - previous_right

        if gap > x_gap_threshold:
            if current:
                blocks.append(current)
            current = [word]
        else:
            current.append(word)

        previous_right = float(word["right"])

    if current:
        blocks.append(current)

    return blocks


def words_to_record(
    block_words: List[pd.Series],
    block_number: int,
    page_number: int,
) -> Dict:
    ordered = sorted(block_words, key=lambda item: float(item["left"]))

    text = " ".join(str(word["text"]) for word in ordered)

    x_start = min(float(word["left"]) for word in ordered)
    x_end = max(float(word["right"]) for word in ordered)
    y_start = min(float(word["top"]) for word in ordered)
    y_end = max(float(word["bottom"]) for word in ordered)

    confidence_values = [
        float(word["conf"])
        for word in ordered
        if pd.notna(word["conf"])
    ]
    average_confidence = (
        sum(confidence_values) / len(confidence_values)
        if confidence_values
        else 0.0
    )

    return {
        "page_number": page_number,
        "block_number": block_number,
        "text": text,
        "x_start": round(x_start, 2),
        "x_end": round(x_end, 2),
        "y_start": round(y_start, 2),
        "y_end": round(y_end, 2),
        "confidence": round(average_confidence, 2),
        "word_count": len(ordered),
    }


def build_blocks(data: pd.DataFrame) -> pd.DataFrame:
    lines = group_words_into_lines(data)
    x_gap_threshold = adaptive_x_gap_threshold(data)

    records: List[Dict] = []

    for line in lines:
        for block_words in split_line_into_blocks(line, x_gap_threshold):
            records.append(
                words_to_record(
                    block_words,
                    block_number=0,
                    page_number=int(block_words[0]["page_number"]),
                )
            )

    blocks = pd.DataFrame(records)

    if blocks.empty:
        return pd.DataFrame(
            columns=[
                "page_number",
                "block_number",
                "text",
                "x_start",
                "x_end",
                "y_start",
                "y_end",
                "confidence",
                "word_count",
            ]
        )

    blocks = blocks.sort_values(
        ["y_start", "x_start"],
        kind="mergesort",
    ).reset_index(drop=True)

    blocks["block_number"] = range(1, len(blocks) + 1)
    return blocks


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value).strip()).lower()


def contains_any(text: str, keywords: Iterable[str]) -> bool:
    normalized = normalize_text(text)
    return any(keyword.lower() in normalized for keyword in keywords)


def detect_section(text: str) -> str:
    """
    Assign a semantic label only when the observed block itself contains
    a meaningful structural marker.

    Order is intentional: more specific markers are evaluated before
    generic markers such as "invoice".
    """
    normalized = normalize_text(text)

    if contains_any(normalized, ["sold by", "seller address", "seller name"]):
        return "SELLER"

    if contains_any(
        normalized,
        ["pan no", "pan number", "gst registration", "gstin", "gst no"],
    ):
        return "SELLER_TAX"

    if contains_any(normalized, ["billing address", "bill to", "billed to"]):
        return "BILLING"

    if contains_any(
        normalized,
        ["shipping address", "ship to", "shipped to", "delivery address"],
    ):
        return "SHIPPING"

    if contains_any(
        normalized,
        ["place of supply", "place of delivery"],
    ):
        return "SUPPLY_DELIVERY"

    if contains_any(normalized, ["order number", "order no", "order date"]):
        return "ORDER"

    if contains_any(
        normalized,
        [
            "invoice number",
            "invoice no",
            "invoice details",
            "invoice date",
        ],
    ):
        return "INVOICE_DETAILS"

    if contains_any(
        normalized,
        [
            "description",
            "unit price",
            "net amount",
            "tax rate",
            "tax type",
            "tax amount",
            "total amount",
            "qty",
        ],
    ):
        return "PRODUCT_TABLE"

    if re.search(r"\btotal\s*:?", normalized):
        return "TOTAL"

    if contains_any(normalized, ["amount in words"]):
        return "AMOUNT_IN_WORDS"

    if contains_any(normalized, ["authorized signatory"]):
        return "SIGNATORY"

    if contains_any(
        normalized,
        [
            "reverse charge",
            "tax is payable",
        ],
    ):
        return "TAX_PAYMENT"

    if contains_any(
        normalized,
        [
            "payment transaction",
            "date & time",
            "invoice value",
            "mode of payment",
            "payment method",
        ],
    ):
        return "PAYMENT"

    return ""


def add_sections(blocks: pd.DataFrame) -> pd.DataFrame:
    result = blocks.copy()
    result["section"] = result["text"].apply(detect_section)
    return result


def save_page_outputs(
    page_blocks: pd.DataFrame,
    page_number: int,
    output_dir: str,
) -> Tuple[str, str]:
    blocks_path = os.path.join(
        output_dir,
        f"invoice_blocks_page_{page_number}.csv",
    )
    sections_path = os.path.join(
        output_dir,
        f"invoice_blocks_with_sections_page_{page_number}.csv",
    )

    page_blocks.to_csv(blocks_path, index=False)

    page_sections = add_sections(page_blocks)
    page_sections.to_csv(sections_path, index=False)

    return blocks_path, sections_path


def print_page_summary(
    page_number: int,
    blocks: pd.DataFrame,
    sections: pd.DataFrame,
) -> None:
    print()
    print("=" * 100)
    print(f"PAGE {page_number} - DETECTED DOCUMENT BLOCKS")
    print("=" * 100)

    for _, row in blocks.iterrows():
        print(
            f"BLOCK {int(row['block_number']):03d} | "
            f"X={row['x_start']:>7.1f}-{row['x_end']:<7.1f} | "
            f"Y={row['y_start']:>7.1f} | "
            f"CONF={row['confidence']:>5.1f} | "
            f"{row['text']}"
        )

    print()
    print("-" * 100)
    print(f"PAGE {page_number} - SECTION RESULTS")
    print("-" * 100)

    for _, row in sections.iterrows():
        if str(row["section"]).strip():
            print(
                f"BLOCK {int(row['block_number']):03d} | "
                f"SECTION={row['section']:<20} | "
                f"{row['text']}"
            )


def main() -> int:
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print()
    print("=" * 100)
    print("UNIVERSAL LAYOUT ANALYSIS")
    print("=" * 100)
    print(f"Working directory : {os.path.abspath(WORK_DIR)}")

    ocr_files = discover_ocr_files(WORK_DIR)

    if not ocr_files:
        print()
        print("❌ No OCR coordinate files found.")
        print(
            "Expected files such as: "
            f"{os.path.join(WORK_DIR, 'page_1_ocr_data.csv')}"
        )
        print()
        print("Run pdf_ocr.py first.")
        return 1

    print(f"Detected OCR pages : {len(ocr_files)}")

    all_page_blocks: List[pd.DataFrame] = []
    first_page_blocks: Optional[pd.DataFrame] = None

    for ocr_path in ocr_files:
        page_number = natural_page_key(ocr_path)

        print()
        print("=" * 100)
        print(f"PROCESSING PAGE {page_number}")
        print("=" * 100)
        print(f"OCR input : {os.path.abspath(ocr_path)}")

        try:
            raw = pd.read_csv(ocr_path)
            data = clean_ocr_data(raw, page_number)
        except Exception as exc:
            print(f"❌ Failed to load page {page_number}: {exc}")
            return 1

        print(f"Detected words : {len(data)}")
        print(
            f"Adaptive Y tolerance : "
            f"{adaptive_y_tolerance(data):.2f}px"
        )
        print(
            f"Adaptive X gap threshold : "
            f"{adaptive_x_gap_threshold(data):.2f}px"
        )

        blocks = build_blocks(data)
        sections = add_sections(blocks)

        if page_number == 1:
            first_page_blocks = blocks.copy()

        save_page_outputs(
            blocks,
            page_number,
            OUTPUT_DIR,
        )

        print_page_summary(page_number, blocks, sections)

        all_page_blocks.append(sections)

    # ------------------------------------------------------------
    # Page-aware aggregate output.
    # Block numbers are intentionally local to each page. The
    # page_number column disambiguates them.
    # ------------------------------------------------------------
    if all_page_blocks:
        all_sections = pd.concat(
            all_page_blocks,
            ignore_index=True,
        )

        all_blocks = all_sections.drop(
            columns=["section"],
            errors="ignore",
        )

        all_blocks_path = os.path.join(
            OUTPUT_DIR,
            "invoice_blocks_all_pages.csv",
        )
        all_sections_path = os.path.join(
            OUTPUT_DIR,
            "invoice_blocks_with_sections_all_pages.csv",
        )

        all_blocks.to_csv(all_blocks_path, index=False)
        all_sections.to_csv(all_sections_path, index=False)

        print()
        print("=" * 100)
        print("PAGE-AWARE AGGREGATE OUTPUT")
        print("=" * 100)
        print(f"✅ All blocks saved    : {all_blocks_path}")
        print(f"✅ All sections saved  : {all_sections_path}")

    # ------------------------------------------------------------
    # Backward compatibility:
    # Keep the original filenames as page-1 outputs because the
    # current spatial_parser expects these exact names. The next
    # spatial_parser revision can consume the all-pages file.
    # ------------------------------------------------------------
    if first_page_blocks is not None:
        page1_sections = add_sections(first_page_blocks)

        legacy_blocks_path = os.path.join(
            OUTPUT_DIR,
            "invoice_blocks.csv",
        )
        legacy_sections_path = os.path.join(
            OUTPUT_DIR,
            "invoice_blocks_with_sections.csv",
        )

        first_page_blocks.to_csv(
            legacy_blocks_path,
            index=False,
        )
        page1_sections.to_csv(
            legacy_sections_path,
            index=False,
        )

        print()
        print("=" * 100)
        print("BACKWARD-COMPATIBILITY OUTPUT")
        print("=" * 100)
        print(
            "Page 1 remains available at the original filenames so the "
            "current downstream parser is not broken."
        )
        print(f"✅ {legacy_blocks_path}")
        print(f"✅ {legacy_sections_path}")

    print()
    print("=" * 100)
    print("LAYOUT ANALYSIS COMPLETE")
    print("=" * 100)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
