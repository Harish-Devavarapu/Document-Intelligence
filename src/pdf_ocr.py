import os
import sys
import re
import shutil

import cv2
import pymupdf
import pandas as pd
import numpy as np
import pytesseract


# ============================================================
# UNIVERSAL DOCUMENT OCR / TEXT EXTRACTION
# ============================================================
#
# Pipeline contract:
#
#   PDF / IMAGE
#       |
#       +--> Digital PDF with usable text layer
#       |       -> native PDF text + native word coordinates
#       |
#       +--> Scanned PDF / IMAGE
#               -> Tesseract OCR + preprocessing variants
#               -> OCR word coordinates + confidence
#
# The downstream pipeline continues to receive:
#
#   page_N.png
#   page_N_ocr.txt
#   page_N_ocr_data.csv
#   page_N_ocr_debug.png
#
# No Amazon/company/document-specific coordinates or fields are used.
#


DEFAULT_OUTPUT_DIR = "output"

PDF_EXTENSIONS = {".pdf"}

IMAGE_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".tif",
    ".tiff",
    ".webp",
}

PDF_RENDER_DPI = 300

# OCR is used for scanned PDFs and image documents.
OCR_CONFIGS = [
    ("psm3", "--oem 3 --psm 3"),
    ("psm6", "--oem 3 --psm 6"),
]

# Sparse-text OCR is deliberately a fallback, not part of every OCR run.
# It is useful for posters, screenshots, forms and documents where text is
# separated by large visual regions.
TARGETED_OCR_CONFIGS = [
    ("psm11", "--oem 3 --psm 11"),
    ("psm6", "--oem 3 --psm 6"),
]


# ============================================================
# TESSERACT
# ============================================================

def configure_tesseract():
    """
    Resolve Tesseract without depending on one developer's PC path.
    """
    candidates = []

    configured = os.environ.get("TESSERACT_CMD", "").strip()
    if configured:
        candidates.append(configured)

    path_tesseract = shutil.which("tesseract")
    if path_tesseract:
        candidates.append(path_tesseract)

    candidates.extend(
        [
            r"C:\Program Files\Tesseract-OCR\tesseract.exe",
            r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        ]
    )

    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            pytesseract.pytesseract.tesseract_cmd = candidate
            print(f"Using Tesseract: {candidate}")
            return candidate

    if path_tesseract:
        pytesseract.pytesseract.tesseract_cmd = path_tesseract
        print(f"Using Tesseract from PATH: {path_tesseract}")
        return path_tesseract

    raise RuntimeError(
        "Tesseract OCR executable was not found. "
        "Install Tesseract OCR or set TESSERACT_CMD."
    )


TESSERACT_CMD = configure_tesseract()


# ============================================================
# INPUT / OUTPUT
# ============================================================

def get_input_path():
    if len(sys.argv) > 1 and sys.argv[1].strip():
        input_path = sys.argv[1].strip()

        print()
        print("=" * 100)
        print("INPUT PROVIDED BY MAIN PIPELINE")
        print("=" * 100)
        print(f"Input path: {input_path}")

        return input_path

    input_path = os.path.join(
        "input",
        "Table.pdf",
    )

    print()
    print("=" * 100)
    print("NO INPUT PROVIDED")
    print("=" * 100)
    print(f"Using default input: {input_path}")

    return input_path


def get_output_dir():
    r"""
    Create one isolated output folder per input document.

    Examples:

        python src\pdf_ocr.py input\Bata_Sandels.pdf
            -> output\Bata_Sandels\

        python src\pdf_ocr.py input\LensCap.pdf
            -> output\LensCap\

    A second argument is still supported for backward compatibility. When
    supplied, it is treated as the exact output directory requested by the
    caller. This keeps the existing pipeline usable while making the normal
    one-argument workflow automatic and collision-safe.
    """

    if len(sys.argv) > 2 and sys.argv[2].strip():
        output_dir = sys.argv[2].strip()

        print()
        print("=" * 100)
        print("OUTPUT DIRECTORY PROVIDED")
        print("=" * 100)
        print(f"Output directory: {output_dir}")

    else:
        input_name = os.path.basename(INPUT_PATH)
        input_stem = os.path.splitext(input_name)[0]

        # Windows-invalid filename characters are replaced so the same
        # input-derived folder naming works safely across platforms.
        safe_stem = re.sub(
            r'[<>:"/\\|?*]',
            "_",
            input_stem,
        ).strip(" .")

        if not safe_stem:
            safe_stem = "document"

        output_dir = os.path.join(
            DEFAULT_OUTPUT_DIR,
            safe_stem,
        )

        print()
        print("=" * 100)
        print("AUTOMATIC OUTPUT DIRECTORY")
        print("=" * 100)
        print(f"Input file name : {input_name}")
        print(f"Output directory: {output_dir}")

    os.makedirs(
        output_dir,
        exist_ok=True,
    )

    return output_dir


INPUT_PATH = get_input_path()
OUTPUT_DIR = get_output_dir()


# ============================================================
# TEXT HELPERS
# ============================================================

def clean_text(text):
    """
    Normalize whitespace while preserving useful line boundaries.
    """
    if text is None:
        return ""

    text = str(text).replace(
        "\x00",
        "",
    )

    cleaned_lines = []

    for line in text.splitlines():
        line = re.sub(
            r"[ \t]+",
            " ",
            line,
        ).strip()

        cleaned_lines.append(line)

    cleaned = "\n".join(cleaned_lines)

    cleaned = re.sub(
        r"\n{3,}",
        "\n\n",
        cleaned,
    )

    return cleaned.strip()


def text_quality_score(text):
    """
    Generic OCR quality heuristic.

    It does not know about Amazon, invoices, receipts, or specific fields.
    """
    text = clean_text(text)

    if not text:
        return 0.0

    characters = len(text)

    alphanumeric = sum(
        character.isalnum()
        for character in text
    )

    lines = [
        line
        for line in text.splitlines()
        if line.strip()
    ]

    if not lines:
        return 0.0

    alpha_ratio = (
        alphanumeric / max(characters, 1)
    )

    line_score = min(
        len(lines) / 20.0,
        1.0,
    )

    length_score = min(
        characters / 1500.0,
        1.0,
    )

    return (
        alpha_ratio * 40.0
        + line_score * 25.0
        + length_score * 35.0
    )


def mean_ocr_confidence(data):
    if data is None or data.empty:
        return 0.0

    if "conf" not in data.columns:
        return 0.0

    values = pd.to_numeric(
        data["conf"],
        errors="coerce",
    )

    values = values[
        values >= 0
    ]

    if values.empty:
        return 0.0

    return float(
        values.mean()
    )


# ============================================================
# DIGITAL PDF DETECTION
# ============================================================

def extract_native_pdf_text(page):
    """
    Read the PDF's own text layer.

    This is preferable to OCR when the PDF contains reliable digital text.
    """
    try:
        return clean_text(
            page.get_text("text")
        )
    except Exception:
        return ""


def native_text_is_usable(text):
    """
    Conservative generic test for whether a PDF has enough text to use
    its native text layer.

    This is not document-specific.
    """
    text = clean_text(text)

    if not text:
        return False

    characters = len(text)

    non_whitespace = sum(
        not character.isspace()
        for character in text
    )

    lines = [
        line
        for line in text.splitlines()
        if line.strip()
    ]

    if characters < 80:
        return False

    if len(lines) < 2:
        return False

    if non_whitespace < 60:
        return False

    return True


# ============================================================
# NATIVE PDF WORD COORDINATES
# ============================================================

def extract_native_pdf_words(
    page,
    image_width,
    image_height,
    page_number,
):
    """
    Extract word-level coordinates directly from a digital PDF.

    PyMuPDF gives PDF coordinates in points. They are converted into the
    pixel coordinate system of the rendered page image so downstream
    spatial/layout modules can use the same coordinate convention as
    Tesseract output.

    Native PDF text has no OCR confidence value, so confidence is set to
    100.0 and the source column identifies it as native text.
    """
    rows = []

    try:
        words = page.get_text(
            "words",
            sort=False,
        )
    except Exception:
        words = []

    if not words:
        return pd.DataFrame(
            columns=[
                "level",
                "page_num",
                "block_num",
                "par_num",
                "line_num",
                "word_num",
                "left",
                "top",
                "width",
                "height",
                "conf",
                "text",
                "source",
            ]
        )

    page_rect = page.rect

    page_width = float(
        page_rect.width or 1
    )

    page_height = float(
        page_rect.height or 1
    )

    scale_x = (
        float(image_width)
        / page_width
    )

    scale_y = (
        float(image_height)
        / page_height
    )

    for word in words:
        if len(word) < 8:
            continue

        x0, y0, x1, y1, text, block_no, line_no, word_no = word[:8]

        text = str(text).strip()

        if not text:
            continue

        left = int(
            round(x0 * scale_x)
        )

        top = int(
            round(y0 * scale_y)
        )

        right = int(
            round(x1 * scale_x)
        )

        bottom = int(
            round(y1 * scale_y)
        )

        rows.append(
            {
                "level": 5,
                "page_num": page_number + 1,
                "block_num": int(block_no),
                "par_num": 0,
                "line_num": int(line_no),
                "word_num": int(word_no),
                "left": left,
                "top": top,
                "width": max(0, right - left),
                "height": max(0, bottom - top),
                "conf": 100.0,
                "text": text,
                "source": "native_pdf",
            }
        )

    return pd.DataFrame(rows)


# ============================================================
# IMAGE PREPROCESSING
# ============================================================

def prepare_for_ocr(image):
    """
    Create generic preprocessing variants.

    No document-specific crop or coordinate is used.
    """
    gray = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2GRAY,
    )

    denoised = cv2.fastNlMeansDenoising(
        gray,
        None,
        h=10,
        templateWindowSize=7,
        searchWindowSize=21,
    )

    otsu = cv2.threshold(
        denoised,
        0,
        255,
        cv2.THRESH_BINARY + cv2.THRESH_OTSU,
    )[1]

    adaptive = cv2.adaptiveThreshold(
        denoised,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        11,
    )

    return {
        "original": image,
        "grayscale": gray,
        "denoised": denoised,
        "otsu": otsu,
        "adaptive": adaptive,
    }


def prepare_targeted_ocr_variants(image):
    """
    Build a small set of stronger, generic fallbacks for difficult images.

    These variants are intentionally used only after the normal OCR pass
    indicates that recognition is weak. They are useful for screenshots,
    posters, phone photos, uneven lighting and low-contrast text.
    """
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Local contrast enhancement helps text sitting on gradients or
    # non-uniform backgrounds without assuming a particular document type.
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    enhanced = clahe.apply(gray)

    # Estimate the slowly changing background and normalize it. This can
    # recover dark/light text when the page background is uneven.
    background = cv2.GaussianBlur(enhanced, (0, 0), 21)
    normalized = cv2.divide(enhanced, background, scale=255)

    # A conservative adaptive threshold is useful when the original image
    # has a strong background gradient.
    adaptive = cv2.adaptiveThreshold(
        enhanced,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        41,
        9,
    )

    # Generic foreground-isolation fallbacks. These are especially useful
    # for screenshots/posters where text is bright and the background is
    # colorful. The thresholds describe image characteristics, not document
    # content or a known template.
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    bright_foreground = cv2.inRange(
        hsv,
        np.array([0, 0, 200], dtype=np.uint8),
        np.array([179, 60, 255], dtype=np.uint8),
    )
    dark_foreground = cv2.inRange(
        hsv,
        np.array([0, 0, 0], dtype=np.uint8),
        np.array([179, 65, 120], dtype=np.uint8),
    )

    cleanup_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (3, 3),
    )
    bright_foreground = cv2.morphologyEx(
        bright_foreground,
        cv2.MORPH_CLOSE,
        cleanup_kernel,
    )
    dark_foreground = cv2.morphologyEx(
        dark_foreground,
        cv2.MORPH_CLOSE,
        cleanup_kernel,
    )

    # Generic light-text extraction using perceptual lightness. This is useful
    # for bright lettering over colorful/gradient backgrounds where an HSV
    # saturation filter can miss parts of large characters.
    lab = cv2.cvtColor(image, cv2.COLOR_BGR2LAB)
    lightness = lab[:, :, 0]
    light_text = cv2.threshold(
        lightness,
        220,
        255,
        cv2.THRESH_BINARY,
    )[1]
    light_text = cv2.morphologyEx(
        light_text,
        cv2.MORPH_CLOSE,
        cleanup_kernel,
    )

    # Small images often contain text that occupies only a few pixels per
    # character. A generic 2x OCR image gives Tesseract more character detail.
    # It is used only for low-resolution inputs and coordinates are mapped
    # back to the original page below.
    max_dimension = max(image.shape[:2])
    upscaled = None
    if max_dimension < 1800:
        upscaled = cv2.resize(
            image,
            None,
            fx=2.0,
            fy=2.0,
            interpolation=cv2.INTER_CUBIC,
        )

    result = {
        "clahe": enhanced,
        "background_normalized": normalized,
        "targeted_adaptive": adaptive,
        "bright_foreground": bright_foreground,
        "dark_foreground": dark_foreground,
        "light_text_threshold": light_text,
    }

    if upscaled is not None:
        result["upscaled_2x"] = upscaled

    return result


def ocr_needs_targeted_retry(text, data, image=None):
    """
    Decide whether generic fallback OCR is justified.

    This is based only on observable OCR quality, never on document names
    or business-specific vocabulary.
    """
    score = ocr_text_quality_score(text, data)
    confidence = mean_ocr_confidence(data)
    word_count = len(data) if data is not None else 0

    cleaned = str(text or "").strip()
    tokens = re.findall(r"\S+", cleaned)
    single_char_tokens = sum(1 for token in tokens if len(token) == 1)
    single_char_ratio = single_char_tokens / max(len(tokens), 1)

    # Trigger only when recognition is actually weak or suspicious.
    small_source = False
    if image is not None and getattr(image, "shape", None):
        small_source = max(image.shape[:2]) < 1000

    return (
        score < 52.0
        or confidence < 48.0
        or word_count < 8
        or single_char_ratio > 0.35
        or small_source
    )


# ============================================================
# TESSERACT OCR
# ============================================================

def run_single_ocr(
    image,
    config,
):
    text = pytesseract.image_to_string(
        image,
        config=config,
    )

    cleaned = clean_text(
        text
    )

    data = pytesseract.image_to_data(
        image,
        config=config,
        output_type=pytesseract.Output.DATAFRAME,
    )

    if data is None:
        data = pd.DataFrame()

    if not data.empty:
        data = data.dropna(
            subset=["text"],
            how="any",
        ).copy()

        data["text"] = (
            data["text"]
            .astype(str)
            .str.strip()
        )

        data = data[
            data["text"] != ""
        ].copy()

        data["conf"] = pd.to_numeric(
            data["conf"],
            errors="coerce",
        )

        data["source"] = "tesseract"

    return cleaned, data



def _normalize_ocr_token(value):
    value = str(value or "").strip().lower()
    value = re.sub(r"[^a-z0-9₹$€£%./:+-]+", "", value)
    return value


def _bbox_iou(a, b):
    ax1 = float(a.get("left", 0))
    ay1 = float(a.get("top", 0))
    ax2 = ax1 + float(a.get("width", 0))
    ay2 = ay1 + float(a.get("height", 0))

    bx1 = float(b.get("left", 0))
    by1 = float(b.get("top", 0))
    bx2 = bx1 + float(b.get("width", 0))
    by2 = by1 + float(b.get("height", 0))

    ix1 = max(ax1, bx1)
    iy1 = max(ay1, by1)
    ix2 = min(ax2, bx2)
    iy2 = min(ay2, by2)

    iw = max(0.0, ix2 - ix1)
    ih = max(0.0, iy2 - iy1)
    intersection = iw * ih

    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - intersection

    if union <= 0:
        return 0.0

    return intersection / union


def _ocr_words_from_tile(image, x0, y0, x1, y1, config, scale=1.5):
    """
    OCR an overlapping horizontal tile and translate its coordinates back
    into the original page coordinate system.

    Tiling is deliberately generic. It does not assume invoices, tables,
    companies, or fixed document coordinates.
    """
    tile = image[y0:y1, x0:x1]

    if tile.size == 0:
        return pd.DataFrame()

    # Upscaling small text inside a tile improves recognition without
    # changing the document-specific geometry.
    tile_up = cv2.resize(
        tile,
        None,
        fx=scale,
        fy=scale,
        interpolation=cv2.INTER_CUBIC,
    )

    try:
        data = pytesseract.image_to_data(
            tile_up,
            config=config,
            output_type=pytesseract.Output.DATAFRAME,
        )
    except Exception:
        return pd.DataFrame()

    if data is None or data.empty:
        return pd.DataFrame()

    data = data.dropna(subset=["text"], how="any").copy()
    data["text"] = data["text"].astype(str).str.strip()
    data = data[data["text"] != ""].copy()

    if data.empty:
        return pd.DataFrame()

    for column in [
        "left", "top", "width", "height",
        "conf", "block_num", "par_num", "line_num", "word_num"
    ]:
        data[column] = pd.to_numeric(
            data.get(column, 0),
            errors="coerce",
        ).fillna(0)

    data["left"] = (data["left"] / scale + x0).round().astype(int)
    data["top"] = (data["top"] / scale + y0).round().astype(int)
    data["width"] = (data["width"] / scale).round().astype(int)
    data["height"] = (data["height"] / scale).round().astype(int)
    data["page_num"] = 1
    data["level"] = 5
    data["source"] = "tesseract_tile"

    return data[
        [
            "level", "page_num", "block_num", "par_num",
            "line_num", "word_num", "left", "top", "width",
            "height", "conf", "text", "source"
        ]
    ].copy()


def run_overlapping_tile_ocr(image):
    """
    Recover small text that a full-page OCR pass can miss.

    The page is divided into overlapping horizontal bands. This is a
    document-agnostic recovery pass; it is not an invoice/table crop.
    """
    height, width = image.shape[:2]

    if height < 400 or width < 400:
        return pd.DataFrame()

    # Four broad horizontal bands with overlap. Keeping full width preserves
    # normal reading order for forms and multi-column documents.
    band_count = 4
    overlap = 0.12
    band_height = height / band_count

    frames = []

    for index in range(band_count):
        y0 = int(max(0, round(index * band_height - band_height * overlap)))
        y1 = int(min(height, round((index + 1) * band_height + band_height * overlap)))

        if y1 <= y0:
            continue

        frame = _ocr_words_from_tile(
            image,
            0,
            y0,
            width,
            y1,
            "--oem 3 --psm 6",
        )

        if not frame.empty:
            frame["tile_index"] = index
            frames.append(frame)

    if not frames:
        return pd.DataFrame()

    return pd.concat(frames, ignore_index=True)



def detect_horizontal_rule_bands(image):
    """
    Detect repeated horizontal rules generically.

    This is useful for forms/tables whose small text is too dense for a
    full-page OCR layout. No fixed document coordinates are used.
    """
    if image is None or image.size == 0:
        return []

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    binary = cv2.threshold(
        gray,
        220,
        255,
        cv2.THRESH_BINARY_INV,
    )[1]

    height, width = gray.shape[:2]

    kernel_width = max(
        40,
        int(round(width * 0.035)),
    )

    horizontal_kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (kernel_width, 1),
    )

    horizontal = cv2.morphologyEx(
        binary,
        cv2.MORPH_OPEN,
        horizontal_kernel,
    )

    row_strength = horizontal.sum(axis=1)

    # Require a line to span a meaningful fraction of the page width.
    threshold = 255 * max(
        25,
        int(width * 0.025),
    )

    positions = np.where(
        row_strength >= threshold
    )[0]

    if len(positions) == 0:
        return []

    groups = []
    start = previous = int(positions[0])

    for position in positions[1:]:
        position = int(position)

        if position > previous + 1:
            groups.append(
                (start, previous)
            )
            start = position

        previous = position

    groups.append(
        (start, previous)
    )

    centers = [
        int(round((start + end) / 2.0))
        for start, end in groups
    ]

    # Collapse lines that are extremely close together.
    collapsed = []

    for center in centers:
        if not collapsed or center - collapsed[-1] > max(3, int(height * 0.001)):
            collapsed.append(center)

    if len(collapsed) < 3:
        return []

    gaps = np.diff(collapsed)

    if len(gaps) == 0:
        return []

    median_gap = float(np.median(gaps))

    if median_gap <= 0:
        return []

    # Keep adjacent rules that plausibly delimit rows. Very large gaps are
    # still allowed because forms often contain a section header followed by
    # a larger structured area.
    bands = []

    for index in range(len(collapsed) - 1):
        top_rule = collapsed[index]
        bottom_rule = collapsed[index + 1]

        gap = bottom_rule - top_rule

        if gap < max(18, int(median_gap * 0.35)):
            continue

        y0 = min(
            height,
            max(0, top_rule + 4),
        )
        y1 = min(
            height,
            max(0, bottom_rule - 4),
        )

        if y1 - y0 >= 20:
            bands.append(
                (y0, y1)
            )

    return bands


def run_horizontal_rule_ocr(image):
    """
    OCR each automatically detected rule-delimited band.

    This is a generic recovery pass for forms/tables with dense small text.
    """
    bands = detect_horizontal_rule_bands(image)

    if not bands:
        return pd.DataFrame()

    height, width = image.shape[:2]
    frames = []

    for band_index, (y0, y1) in enumerate(bands):
        frame = _ocr_words_from_tile(
            image,
            0,
            y0,
            width,
            y1,
            "--oem 3 --psm 6",
            scale=2.0,
        )

        if frame.empty:
            continue

        frame["rule_band_index"] = band_index
        frame["source"] = "tesseract_rule_band"
        frames.append(frame)

    if not frames:
        return pd.DataFrame()

    return pd.concat(
        frames,
        ignore_index=True,
    )

def merge_ocr_word_observations(base_data, tile_data):
    """
    Merge tile observations into the selected full-page OCR observations.

    Existing words are preserved. A tile word is added only when it does
    not substantially overlap an existing word with the same/similar text.
    This prevents overlapping tiles from producing repeated text while
    allowing genuinely missed words to be recovered.
    """
    base = normalize_coordinate_columns(base_data)
    tile = normalize_coordinate_columns(tile_data)

    if tile.empty:
        return base

    if base.empty:
        return tile

    base_rows = base.to_dict("records")

    # Spatial indexing by coarse y bands keeps the generic merge efficient.
    buckets = {}
    bucket_height = 80

    for idx, row in enumerate(base_rows):
        key = int(float(row.get("top", 0)) // bucket_height)
        buckets.setdefault(key, []).append(idx)

    for _, candidate in tile.iterrows():
        token = _normalize_ocr_token(candidate.get("text", ""))
        if not token:
            continue

        top = float(candidate.get("top", 0))
        key = int(top // bucket_height)

        candidate_record = candidate.to_dict()
        duplicate = False
        duplicate_idx = None

        for neighbor_key in (key - 1, key, key + 1):
            for idx in buckets.get(neighbor_key, []):
                existing = base_rows[idx]
                existing_token = _normalize_ocr_token(existing.get("text", ""))

                if not existing_token:
                    continue

                overlap = _bbox_iou(existing, candidate_record)

                if overlap < 0.45:
                    continue

                # Very strong spatial overlap means the two OCR passes are
                # describing the same visual word, even when Tesseract
                # produced different text for it. Prefer the higher-quality
                # observation instead of displaying both.
                if overlap >= 0.78:
                    duplicate = True
                    duplicate_idx = idx
                    break

                # Exact token match is the next strongest duplicate signal.
                if token == existing_token:
                    duplicate = True
                    duplicate_idx = idx
                    break

                # Short OCR tokens need stronger overlap; longer tokens can
                # tolerate small OCR spelling differences.
                if len(token) >= 4 and len(existing_token) >= 4:
                    from difflib import SequenceMatcher
                    similarity = SequenceMatcher(
                        None,
                        token,
                        existing_token,
                    ).ratio()

                    if similarity >= 0.78 and overlap >= 0.60:
                        duplicate = True
                        duplicate_idx = idx
                        break

            if duplicate:
                break

        if duplicate:
            # Prefer the tile observation only when it has clearly better
            # confidence; otherwise keep the original selected OCR result.
            try:
                old_conf = float(base_rows[duplicate_idx].get("conf", 0))
                new_conf = float(candidate_record.get("conf", 0))
            except Exception:
                old_conf = new_conf = 0

            if new_conf > old_conf + 5:
                base_rows[duplicate_idx] = candidate_record

        else:
            base_rows.append(candidate_record)
            new_idx = len(base_rows) - 1
            new_key = int(float(candidate_record.get("top", 0)) // bucket_height)
            buckets.setdefault(new_key, []).append(new_idx)

    merged = pd.DataFrame(base_rows)
    return normalize_coordinate_columns(merged)


def build_generic_word_consensus(selected_data, candidates):
    """
    Correct selected OCR words using spatial consensus from independent OCR
    observations. Only overlapping observations are compared; no vocabulary,
    document type, company name, or field list is used.
    """
    base = normalize_coordinate_columns(selected_data)
    if base.empty or not candidates:
        return base

    candidate_frames = []
    for candidate in candidates:
        frame = normalize_coordinate_columns(candidate.get("data"))
        if frame is not None and not frame.empty:
            candidate_frames.append(frame)

    if not candidate_frames:
        return base

    def token_key(value):
        return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

    def center(row):
        left = float(row.get("left", 0))
        top = float(row.get("top", 0))
        width = float(row.get("width", 0))
        height = float(row.get("height", 0))
        return left + width / 2.0, top + height / 2.0, width, height

    rows = base.to_dict("records")

    for row in rows:
        original = str(row.get("text", "")).strip()
        if not original:
            continue

        cx, cy, width, height = center(row)
        observations = [(original, float(row.get("conf", 0) or 0), 1)]

        for frame in candidate_frames:
            best = None
            best_overlap = 0.0
            for candidate in frame.to_dict("records"):
                candidate_text = str(candidate.get("text", "")).strip()
                if not candidate_text:
                    continue

                overlap = _bbox_iou(row, candidate)
                ccx, ccy, cw, ch = center(candidate)
                distance = ((cx - ccx) ** 2 + (cy - ccy) ** 2) ** 0.5
                tolerance = max(8.0, height, ch) * 1.5

                # Prefer true spatial overlap; allow a small coordinate drift
                # between different preprocessing variants.
                if overlap >= 0.30 or distance <= tolerance:
                    if overlap > best_overlap:
                        best = candidate
                        best_overlap = overlap

            if best is not None:
                observations.append((
                    str(best.get("text", "")).strip(),
                    float(best.get("conf", 0) or 0),
                    1,
                ))

        # Vote on exact normalized observations first. If variants disagree,
        # confidence breaks ties rather than inventing a new spelling.
        groups = {}
        for text, confidence, weight in observations:
            key = token_key(text)
            if not key:
                continue
            groups.setdefault(key, {"count": 0, "confidence": 0.0, "texts": []})
            groups[key]["count"] += weight
            groups[key]["confidence"] += max(0.0, confidence)
            groups[key]["texts"].append(text)

        if not groups:
            continue

        winner_key, winner = max(
            groups.items(),
            key=lambda item: (item[1]["count"], item[1]["confidence"]),
        )

        # Require at least two independent observations before changing a
        # selected token. This keeps a single noisy OCR pass from overriding
        # the selected result.
        if winner["count"] >= 2 and winner_key != token_key(original):
            best_text = max(
                winner["texts"],
                key=lambda value: len(value),
            )
            row["text"] = best_text

    return normalize_coordinate_columns(pd.DataFrame(rows))


def reconstruct_text_from_words(data):
    """
    Reconstruct readable page text from OCR word coordinates.

    The reconstruction is document-agnostic. It uses observed word geometry
    to preserve useful spacing for forms and tables, while keeping normal
    prose readable. It never knows about invoices, Amazon, receipts, or fixed
    document coordinates.
    """
    data = normalize_coordinate_columns(data)
    if data.empty:
        return ""

    words = data.copy()

    if "conf" in words.columns:
        confidence_values = pd.to_numeric(
            words["conf"], errors="coerce"
        ).fillna(0)
        # Do not let obvious OCR noise dominate the enhanced presentation.
        words = words[confidence_values >= 20].copy()
        if words.empty:
            return ""

    for column in ("left", "top", "width", "height"):
        words[column] = pd.to_numeric(
            words[column], errors="coerce"
        ).fillna(0.0)

    words["right"] = words["left"] + words["width"]
    words["center_y"] = words["top"] + words["height"] / 2.0

    valid_heights = words.loc[words["height"] > 0, "height"]
    median_height = float(valid_heights.median()) if not valid_heights.empty else 20.0
    y_tolerance = max(6.0, median_height * 0.55)

    words = words.sort_values(["top", "left"], kind="stable")
    lines = []

    for _, row in words.iterrows():
        cy = float(row["center_y"])
        target = None
        best_distance = None

        for line in lines:
            distance = abs(cy - line["center_y"])
            if distance <= y_tolerance and (
                best_distance is None or distance < best_distance
            ):
                target = line
                best_distance = distance

        if target is None:
            target = {"center_y": cy, "words": []}
            lines.append(target)

        target["words"].append(row.to_dict())
        target["center_y"] = float(
            np.mean([float(item["center_y"]) for item in target["words"]])
        )

    lines.sort(key=lambda line: line["center_y"])

    positive_widths = words.loc[words["width"] > 0, "width"]
    median_word_width = float(positive_widths.median()) if not positive_widths.empty else 20.0
    base_char_width = max(3.0, median_word_width / 5.0)

    output_lines = []

    for line in lines:
        line_words = sorted(line["words"], key=lambda item: float(item.get("left", 0)))
        pieces = []
        previous_right = None

        for word in line_words:
            text = str(word.get("text", "")).strip()
            if not text:
                continue

            left = float(word.get("left", 0))
            right = left + float(word.get("width", 0))

            if previous_right is None:
                pieces.append(text)
            else:
                gap = max(0.0, left - previous_right)
                # Preserve real column-like gaps, but do not create enormous
                # whitespace in ordinary prose.
                spaces = max(1, int(round(gap / base_char_width)))
                # OCR text is for reading first, not pixel-perfect rendering.
                # Very small coordinate gaps can otherwise become enormous
                # runs of spaces on low-resolution receipts/screenshots.
                spaces = min(spaces, 8)
                pieces.append(" " * spaces + text)

            previous_right = max(previous_right or right, right)

        if pieces:
            output_lines.append("".join(pieces).rstrip())

    # IMPORTANT: clean_text() collapses spaces and would destroy the layout
    # we intentionally reconstructed. Only normalize null bytes and excessive
    # blank lines here.
    result = "\n".join(output_lines).replace("\x00", "")

    # Keep the OCR readable in the application. Coordinate reconstruction
    # must never turn a normal receipt/document into a vertically stretched
    # page of isolated words. Preserve at most one intentional blank line.
    result = re.sub(r"[ \t]+\n", "\n", result)
    result = re.sub(r"\n{3,}", "\n\n", result)
    return result.strip()


def ocr_layout_coherence_score(data):
    """
    Score whether OCR observations form readable lines rather than isolated
    words. This is generic layout evidence and is independent of document type.
    """
    if data is None or data.empty:
        return 0.0

    frame = data.copy()
    required = {"text", "top", "height"}
    if not required.issubset(frame.columns):
        return 0.0

    frame["text"] = frame["text"].astype(str).str.strip()
    frame = frame[frame["text"] != ""].copy()
    if frame.empty:
        return 0.0

    # Prefer Tesseract's observed line identifiers when available.
    line_columns = [c for c in ("block_num", "par_num", "line_num") if c in frame.columns]
    if line_columns:
        grouped = frame.groupby(line_columns, dropna=False).size()
        line_sizes = grouped.tolist()
    else:
        # Generic fallback: cluster by vertical center.
        frame["top"] = pd.to_numeric(frame["top"], errors="coerce").fillna(0.0)
        frame["height"] = pd.to_numeric(frame["height"], errors="coerce").fillna(0.0)
        frame["cy"] = frame["top"] + frame["height"] / 2.0
        tolerance = max(5.0, float(frame["height"].median()) * 0.6)
        centers = []
        line_sizes = []
        for cy in sorted(frame["cy"].tolist()):
            matched = False
            for idx, center in enumerate(centers):
                if abs(cy - center) <= tolerance:
                    line_sizes[idx] += 1
                    centers[idx] = (center * (line_sizes[idx] - 1) + cy) / line_sizes[idx]
                    matched = True
                    break
            if not matched:
                centers.append(cy)
                line_sizes.append(1)

    if not line_sizes:
        return 0.0

    average_words = float(np.mean(line_sizes))
    one_word_ratio = sum(size == 1 for size in line_sizes) / len(line_sizes)
    multiword_ratio = sum(size >= 2 for size in line_sizes) / len(line_sizes)

    # Reward coherent multi-word lines and penalize candidates that fragment
    # ordinary text into many isolated one-word lines. Keep the effect modest
    # so actual OCR recognition quality still dominates.
    score = min(average_words / 3.0, 1.0) * 12.0
    score += multiword_ratio * 6.0
    score -= one_word_ratio * 10.0
    return max(0.0, min(18.0, score))


def ocr_text_quality_score(text, data=None):
    """
    Score OCR output using generic observable signals.

    The score rewards readable coverage and structure, but explicitly
    penalizes low-confidence noise, repeated fragments and isolated
    one-character artifacts. It never depends on document vocabulary.
    """
    cleaned = str(text or "").replace("\x00", "").strip()
    if not cleaned:
        return 0.0

    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    if not lines:
        return 0.0

    characters = len(cleaned)
    alphanumeric = sum(ch.isalnum() for ch in cleaned)
    alpha_ratio = alphanumeric / max(characters, 1)

    tokens = re.findall(r"\S+", cleaned)
    normalized_tokens = [token.lower() for token in tokens]
    unique_ratio = len(set(normalized_tokens)) / max(len(tokens), 1)
    average_line_length = characters / max(len(lines), 1)

    score = min(characters / 1800.0, 1.0) * 25.0
    score += min(len(lines) / 25.0, 1.0) * 20.0
    score += min(alpha_ratio, 1.0) * 25.0
    score += unique_ratio * 15.0
    score += min(average_line_length / 80.0, 1.0) * 15.0

    # Generic text-shape penalties. These help prevent an enhanced OCR pass
    # from winning merely because it produced more characters.
    single_char_ratio = sum(
        1 for token in tokens if len(token) == 1
    ) / max(len(tokens), 1)
    punctuation_only_ratio = sum(
        1 for token in tokens if not any(ch.isalnum() for ch in token)
    ) / max(len(tokens), 1)

    score -= min(single_char_ratio, 0.60) * 18.0
    score -= min(punctuation_only_ratio, 0.40) * 12.0

    if data is not None and not data.empty and "conf" in data.columns:
        confidence_values = pd.to_numeric(
            data["conf"],
            errors="coerce",
        )
        confidence_values = confidence_values[confidence_values >= 0]

        if not confidence_values.empty:
            mean_confidence = float(confidence_values.mean())
            low_conf_ratio = float((confidence_values < 40).mean())
            very_low_conf_ratio = float((confidence_values < 20).mean())

            score += min(max(mean_confidence, 0.0), 100.0) * 0.20
            score -= low_conf_ratio * 18.0
            score -= very_low_conf_ratio * 12.0

    return max(0.0, float(score))


def run_ocr_variants(images, targeted=False):
    """
    Run a controlled OCR set and choose the strongest generic result.

    Normal mode is intentionally small for speed. Targeted mode is used
    only when the normal result is weak and adds sparse-text recognition
    against stronger contrast/background-normalized variants.
    """
    results = []

    allowed_images = (
        {"original", "grayscale", "adaptive"}
        if not targeted
        else {
            "original",
            "clahe",
            "background_normalized",
            "targeted_adaptive",
            "bright_foreground",
            "dark_foreground",
            "light_text_threshold",
            "upscaled_2x",
        }
    )

    for image_name, image in images.items():
        if image_name not in allowed_images:
            continue

        if targeted:
            # Sparse-text layout modes are useful on natural/processed
            # images. Foreground masks are intentionally tested with the
            # normal block mode because their connected text is already
            # isolated from much of the background.
            if image_name in {"bright_foreground", "dark_foreground", "light_text_threshold"}:
                configs = [("psm6", "--oem 3 --psm 6")]
            elif image_name == "upscaled_2x":
                configs = TARGETED_OCR_CONFIGS
            else:
                configs = TARGETED_OCR_CONFIGS
        else:
            configs = OCR_CONFIGS

        for config_name, config in configs:
            try:
                text, data = run_single_ocr(
                    image,
                    config,
                )

                if targeted and image_name == "upscaled_2x" and data is not None and not data.empty:
                    for column in ("left", "top", "width", "height"):
                        if column in data.columns:
                            data[column] = pd.to_numeric(
                                data[column], errors="coerce"
                            ).fillna(0) / 2.0
                    data["left"] = data["left"].round().astype(int)
                    data["top"] = data["top"].round().astype(int)
                    data["width"] = data["width"].round().astype(int)
                    data["height"] = data["height"].round().astype(int)

                confidence = mean_ocr_confidence(data)
                quality = ocr_text_quality_score(text, data)
                layout_score = ocr_layout_coherence_score(data)

                # Confidence is useful but cannot dominate the decision:
                # Tesseract can be confidently wrong on complex images.
                # Layout coherence prevents sparse-layout modes from winning
                # simply because they have high confidence when a normal
                # reading-order mode preserves complete lines better.
                combined_score = (
                    quality
                    + min(confidence, 100.0) * 0.15
                    + layout_score
                )

                result = {
                    "image_name": image_name,
                    "config_name": config_name,
                    "config": config,
                    "text": text,
                    "data": data,
                    "confidence": confidence,
                    "score": combined_score,
                }

                results.append(result)

                print(
                    f"  OCR {image_name:<22} "
                    f"{config_name:<5} "
                    f"chars={len(text):<6} "
                    f"words={len(data):<6} "
                    f"confidence={confidence:>6.1f} "
                    f"score={combined_score:>7.1f} "
                    f"layout={layout_score:>5.1f}"
                )

            except Exception as error:
                print(
                    f"  ⚠️ OCR failed for "
                    f"{image_name}/{config_name}: {error}"
                )

    if not results:
        raise RuntimeError(
            "All OCR attempts failed."
        )

    results.sort(
        key=lambda result: result["score"],
        reverse=True,
    )

    return results[0], results


# ============================================================
# FILE HELPERS
# ============================================================

def save_text(
    path,
    text,
):
    with open(
        path,
        "w",
        encoding="utf-8",
    ) as file:
        file.write(
            text or ""
        )


def save_dataframe(
    path,
    data,
):
    if data is None:
        data = pd.DataFrame()

    data.to_csv(
        path,
        index=False,
    )


def normalize_coordinate_columns(
    data,
):
    if data is None:
        return pd.DataFrame()

    data = data.copy()

    required_columns = [
        "level",
        "page_num",
        "block_num",
        "par_num",
        "line_num",
        "word_num",
        "left",
        "top",
        "width",
        "height",
        "conf",
        "text",
        "source",
    ]

    for column in required_columns:
        if column not in data.columns:
            if column == "source":
                data[column] = "unknown"
            elif column == "text":
                data[column] = ""
            else:
                data[column] = 0

    data = data[
        required_columns
    ].copy()

    numeric_columns = [
        "level",
        "page_num",
        "block_num",
        "par_num",
        "line_num",
        "word_num",
        "left",
        "top",
        "width",
        "height",
        "conf",
    ]

    for column in numeric_columns:
        data[column] = pd.to_numeric(
            data[column],
            errors="coerce",
        )

    data["text"] = (
        data["text"]
        .astype(str)
        .str.strip()
    )

    data = data[
        data["text"] != ""
    ].copy()

    return data


# ============================================================
# PDF PAGE RENDERING
# ============================================================

def render_pdf_page(
    page,
    page_number,
):
    scale = (
        PDF_RENDER_DPI / 72.0
    )

    matrix = pymupdf.Matrix(
        scale,
        scale,
    )

    pix = page.get_pixmap(
        matrix=matrix,
        alpha=False,
        colorspace=pymupdf.csRGB,
    )

    image_path = os.path.join(
        OUTPUT_DIR,
        f"page_{page_number + 1}.png",
    )

    pix.save(
        image_path
    )

    return image_path


# ============================================================
# DEBUG IMAGE
# ============================================================

def create_debug_image(
    image,
    data,
    path,
):
    debug_image = image.copy()

    if data is None:
        data = pd.DataFrame()

    for _, row in data.iterrows():

        text = str(
            row.get(
                "text",
                "",
            )
        ).strip()

        if not text:
            continue

        x = int(
            row.get(
                "left",
                0,
            )
            or 0
        )

        y = int(
            row.get(
                "top",
                0,
            )
            or 0
        )

        width = int(
            row.get(
                "width",
                0,
            )
            or 0
        )

        height = int(
            row.get(
                "height",
                0,
            )
            or 0
        )

        cv2.rectangle(
            debug_image,
            (x, y),
            (
                x + width,
                y + height,
            ),
            (0, 255, 0),
            2,
        )

        cv2.putText(
            debug_image,
            text,
            (
                x,
                max(
                    20,
                    y - 5,
                ),
            ),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (0, 0, 255),
            1,
            cv2.LINE_AA,
        )

    cv2.imwrite(
        path,
        debug_image,
    )


# ============================================================
# OCR DIAGNOSTICS
# ============================================================

def save_diagnostics(
    path,
    selected_name,
    selected_confidence,
    canonical_source,
    canonical_text,
    results=None,
):
    with open(
        path,
        "w",
        encoding="utf-8",
    ) as file:

        file.write(
            "=" * 100 + "\n"
        )

        file.write(
            "OCR / TEXT EXTRACTION DIAGNOSTICS\n"
        )

        file.write(
            "=" * 100 + "\n\n"
        )

        file.write(
            f"Selected source: {selected_name}\n"
        )

        file.write(
            f"Selected confidence: "
            f"{selected_confidence:.2f}\n"
        )

        file.write(
            f"Canonical text source: "
            f"{canonical_source}\n\n"
        )

        if results:
            for index, result in enumerate(
                results,
                start=1,
            ):
                file.write(
                    "-" * 100 + "\n"
                )

                file.write(
                    f"RESULT {index}: "
                    f"{result['image_name']} / "
                    f"{result['config_name']}\n"
                )

                file.write(
                    f"Confidence: "
                    f"{result['confidence']:.2f}\n"
                )

                file.write(
                    f"Quality score: "
                    f"{result['score']:.2f}\n"
                )

                file.write(
                    "-" * 100 + "\n\n"
                )

                file.write(
                    result["text"]
                )

                file.write(
                    "\n\n"
                )

        file.write(
            "=" * 100 + "\n"
        )

        file.write(
            "CANONICAL TEXT\n"
        )

        file.write(
            "=" * 100 + "\n\n"
        )

        file.write(
            canonical_text or ""
        )


# ============================================================
# INPUT VALIDATION
# ============================================================

print()
print("=" * 100)
print("OCR WORKING PATHS")
print("=" * 100)
print(
    f"Input path : "
    f"{os.path.abspath(INPUT_PATH)}"
)
print(
    f"Output dir : "
    f"{os.path.abspath(OUTPUT_DIR)}"
)


if not os.path.exists(
    INPUT_PATH
):
    print()
    print(
        "❌ Input file not found:"
    )
    print(
        INPUT_PATH
    )
    sys.exit(1)


INPUT_EXTENSION = os.path.splitext(
    INPUT_PATH
)[1].lower()


if INPUT_EXTENSION in PDF_EXTENSIONS:
    INPUT_TYPE = "PDF"

elif INPUT_EXTENSION in IMAGE_EXTENSIONS:
    INPUT_TYPE = "IMAGE"

else:
    print()
    print(
        "❌ Unsupported input format"
    )
    print(
        f"Extension: {INPUT_EXTENSION}"
    )
    print()
    print(
        "Supported formats:"
    )
    print("  PDF")
    print("  PNG")
    print("  JPG / JPEG")
    print("  BMP")
    print("  TIFF")
    print("  WEBP")
    sys.exit(1)


print()
print("=" * 100)
print("INPUT TYPE DETECTION")
print("=" * 100)
print(
    f"Detected input type: "
    f"{INPUT_TYPE}"
)


# ============================================================
# OPEN PDF
# ============================================================

pdf = None

if INPUT_TYPE == "PDF":

    print()
    print("=" * 100)
    print("OPENING PDF")
    print("=" * 100)

    try:
        pdf = pymupdf.open(
            INPUT_PATH
        )

    except Exception as error:
        print()
        print(
            "❌ Could not open PDF"
        )
        print(
            f"Error: {error}"
        )
        sys.exit(1)

    total_pages = len(
        pdf
    )

    if total_pages == 0:
        print(
            "❌ PDF contains no pages."
        )
        pdf.close()
        sys.exit(1)

else:
    total_pages = 1


print()
print(
    f"Total pages/images to process: "
    f"{total_pages}"
)


# ============================================================
# PROCESS EACH PAGE
# ============================================================

successful_pages = 0

for page_number in range(
    total_pages
):

    print()
    print("=" * 100)
    print(
        f"PROCESSING PAGE "
        f"{page_number + 1}"
    )
    print("=" * 100)

    native_pdf_text = ""
    native_pdf_available = False
    native_data = pd.DataFrame()

    # --------------------------------------------------------
    # PREPARE PAGE IMAGE
    # --------------------------------------------------------

    if INPUT_TYPE == "PDF":

        page = pdf[
            page_number
        ]

        native_pdf_text = (
            extract_native_pdf_text(
                page
            )
        )

        native_pdf_available = (
            native_text_is_usable(
                native_pdf_text
            )
        )

        print()
        print(
            "Rendering PDF page..."
        )

        image_path = render_pdf_page(
            page,
            page_number,
        )

        print(
            f"✅ Page image saved: "
            f"{image_path}"
        )

    else:

        image_path = os.path.join(
            OUTPUT_DIR,
            f"page_{page_number + 1}.png",
        )

        source_image = cv2.imread(
            INPUT_PATH
        )

        if source_image is None:
            print()
            print(
                "❌ Could not load input image"
            )
            print(
                INPUT_PATH
            )
            continue

        cv2.imwrite(
            image_path,
            source_image,
        )

        print(
            f"✅ Input image saved: "
            f"{image_path}"
        )

    # --------------------------------------------------------
    # LOAD RENDERED PAGE
    # --------------------------------------------------------

    image = cv2.imread(
        image_path
    )

    if image is None:
        print(
            "❌ Could not load page image."
        )
        continue

    height, width = image.shape[:2]

    print(
        f"Image size: "
        f"{width} x {height}"
    )

    # --------------------------------------------------------
    # DIGITAL PDF PATH
    # --------------------------------------------------------
    #
    # If the PDF already contains reliable text, use it directly.
    # This avoids degrading clean digital text through OCR and gives
    # us accurate native word coordinates for layout analysis.
    #

    if (
        INPUT_TYPE == "PDF"
        and native_pdf_available
    ):

        print()
        print("=" * 100)
        print("DIGITAL PDF TEXT LAYER DETECTED")
        print("=" * 100)

        print(
            "Using native PDF text as the canonical text source."
        )

        print(
            "Using native PDF word coordinates for layout analysis."
        )

        native_text_path = os.path.join(
            OUTPUT_DIR,
            f"page_{page_number + 1}_native_text.txt",
        )

        save_text(
            native_text_path,
            native_pdf_text,
        )

        native_data = extract_native_pdf_words(
            page,
            width,
            height,
            page_number,
        )

        data = normalize_coordinate_columns(
            native_data
        )

        canonical_text = native_pdf_text
        canonical_source = "native_pdf_text"
        selected_name = "native_pdf_text"
        selected_confidence = 100.0

        # Native text is already reliable, so do not waste time running
        # six Tesseract passes on a digital page. A single OCR fallback
        # is not needed unless the native layer is absent.
        diagnostics_path = os.path.join(
            OUTPUT_DIR,
            f"page_{page_number + 1}_ocr_diagnostics.txt",
        )

        save_diagnostics(
            diagnostics_path,
            selected_name,
            selected_confidence,
            canonical_source,
            canonical_text,
            results=None,
        )

        print()
        print(
            "Native PDF text preview:"
        )
        print(
            canonical_text
        )

    # --------------------------------------------------------
    # OCR PATH
    # --------------------------------------------------------

    else:

        print()
        print("=" * 100)
        print("RUNNING OCR")
        print("=" * 100)

        variants = prepare_for_ocr(
            image
        )

        for variant_name in (
            "grayscale",
            "denoised",
            "otsu",
            "adaptive",
        ):

            variant_path = os.path.join(
                OUTPUT_DIR,
                f"page_{page_number + 1}_{variant_name}.png",
            )

            cv2.imwrite(
                variant_path,
                variants[variant_name],
            )

            print(
                f"✅ Saved {variant_name}: "
                f"{variant_path}"
            )

        selected, all_results = run_ocr_variants(
            variants,
            targeted=False,
        )

        # Difficult-image fallback: only spend the additional OCR time when
        # the normal pass is weak. This keeps ordinary invoices/documents
        # fast while giving screenshots, posters, forms and low-contrast
        # images another recognition path.
        targeted_results = []
        if ocr_needs_targeted_retry(
            selected["text"],
            selected["data"],
            image=image,
        ):
            print()
            print("Normal OCR quality is weak; running targeted generic OCR fallback.")

            targeted_variants = prepare_targeted_ocr_variants(image)
            targeted_selected, targeted_results = run_ocr_variants(
                {
                    "original": image,
                    **targeted_variants,
                },
                targeted=True,
            )

            if targeted_selected["score"] > selected["score"] + 2.0:
                selected = targeted_selected
                all_results.extend(targeted_results)
                print(
                    f"Targeted OCR selected: {selected['image_name']} / "
                    f"{selected['config_name']}"
                )
            else:
                all_results.extend(targeted_results)
                print("Normal OCR retained after targeted fallback comparison.")
        else:
            print("Normal OCR quality is sufficient; targeted fallback skipped.")

        canonical_text = selected[
            "text"
        ]

        canonical_source = "tesseract"
        selected_name = (
            f"{selected['image_name']} / "
            f"{selected['config_name']}"
        )

        selected_confidence = float(
            selected["confidence"]
        )

        data = normalize_coordinate_columns(
            selected["data"]
        )

        # ----------------------------------------------------
        # GENERIC SMALL-TEXT RECOVERY
        # ----------------------------------------------------
        #
        # Keep the existing canonical OCR result unchanged for
        # backward compatibility. In addition, run an overlapping
        # full-width tile pass to recover small text that a
        # full-page Tesseract layout may miss.
        #
        # The merged result is stored separately and is intended
        # for generic text presentation / inspection.
        #
        try:
            tile_data = run_overlapping_tile_ocr(image)
            rule_band_data = run_horizontal_rule_ocr(image)

            recovery_frames = [
                frame
                for frame in (
                    tile_data,
                    rule_band_data,
                )
                if frame is not None and not frame.empty
            ]

            if recovery_frames:
                recovery_data = pd.concat(
                    recovery_frames,
                    ignore_index=True,
                )
            else:
                recovery_data = pd.DataFrame()

            enhanced_data = merge_ocr_word_observations(
                data,
                recovery_data,
            )
        except Exception as error:
            print(
                f"  ⚠️ Enhanced OCR recovery skipped: {error}"
            )
            enhanced_data = data.copy()

        # Correct obvious character-level disagreements using independent OCR
        # observations, while preserving the selected candidate's geometry.
        consensus_data = build_generic_word_consensus(
            data,
            all_results,
        )

        consensus_text = reconstruct_text_from_words(
            consensus_data
        )
        enhanced_text = reconstruct_text_from_words(
            enhanced_data
        )

        enhanced_text_path = os.path.join(
            OUTPUT_DIR,
            f"page_{page_number + 1}_ocr_enhanced.txt",
        )

        save_text(
            enhanced_text_path,
            enhanced_text or canonical_text,
        )

        # Promote enhanced OCR only when it is demonstrably stronger by
        # generic quality signals. Recovery passes can find missed small text,
        # but they can also introduce fragments; blindly replacing the full-
        # page OCR can therefore make a good result worse.
        #
        # Digital PDFs with a usable native text layer never enter this OCR
        # branch, so native PDF behavior remains unchanged.
        if consensus_text.strip():
            consensus_quality = ocr_text_quality_score(consensus_text, consensus_data)
            selected_quality = ocr_text_quality_score(canonical_text, data)
            if consensus_quality >= selected_quality - 5.0:
                # Use consensus only for coordinate observations consumed by
                # downstream extraction. Keep Tesseract's selected native text
                # layout for the human-readable OCR file.
                data = consensus_data.copy()
                canonical_source = "tesseract_consensus_data"
                print(
                    f"Consensus OCR observations applied: selected={selected_quality:.2f} "
                    f"consensus={consensus_quality:.2f}"
                )

        if not enhanced_data.empty and enhanced_text.strip():
            base_quality = ocr_text_quality_score(canonical_text, data)
            enhanced_quality = ocr_text_quality_score(enhanced_text, enhanced_data)

            print(
                f"Enhanced OCR quality: base={base_quality:.2f} "
                f"enhanced={enhanced_quality:.2f}"
            )

            # A modest margin prevents tiny score fluctuations from changing
            # the canonical result. The enhanced artifact is always retained.
            if enhanced_quality >= base_quality + 3.0:
                data = enhanced_data.copy()
                # Use the geometry reconstruction only when it is actually
                # more readable than Tesseract's native text layout. This
                # prevents sparse-layout OCR (for example receipts/posters)
                # from being displayed as widely separated words.
                compact_enhanced = re.sub(r"[ \t]+", " ", enhanced_text)
                compact_enhanced = re.sub(r"\n{3,}", "\n\n", compact_enhanced).strip()
                if compact_enhanced:
                    canonical_text = compact_enhanced
                canonical_source = "tesseract_enhanced"
                print("Enhanced OCR selected as canonical result.")
            else:
                print("Original full-page OCR retained as canonical result.")

        enhanced_data_path = os.path.join(
            OUTPUT_DIR,
            f"page_{page_number + 1}_ocr_enhanced_data.csv",
        )

        save_dataframe(
            enhanced_data_path,
            enhanced_data,
        )

        print(
            f"Enhanced OCR words: {len(enhanced_data)}"
        )

        print(
            f"Tile recovery words: {len(tile_data) if not tile_data.empty else 0}"
        )

        print(
            f"Rule-band recovery words: {len(rule_band_data) if not rule_band_data.empty else 0}"
        )

        print(
            f"Enhanced OCR text saved: {enhanced_text_path}"
        )

        print()
        print(
            f"Selected OCR variant: "
            f"{selected_name}"
        )

        print(
            f"Selected OCR confidence: "
            f"{selected_confidence:.1f}"
        )

        print()
        print(
            "Canonical OCR result:"
        )
        print(
            canonical_text
        )

        diagnostics_path = os.path.join(
            OUTPUT_DIR,
            f"page_{page_number + 1}_ocr_diagnostics.txt",
        )

        save_diagnostics(
            diagnostics_path,
            selected_name,
            selected_confidence,
            canonical_source,
            canonical_text,
            results=all_results,
        )

    # --------------------------------------------------------
    # SAVE CANONICAL TEXT
    # --------------------------------------------------------

    text_path = os.path.join(
        OUTPUT_DIR,
        f"page_{page_number + 1}_ocr.txt",
    )

    save_text(
        text_path,
        canonical_text,
    )

    print()
    print(
        f"✅ Canonical text saved: "
        f"{text_path}"
    )

    # --------------------------------------------------------
    # SAVE COORDINATES
    # --------------------------------------------------------

    print()
    print("=" * 100)
    print("WORD COORDINATES")
    print("=" * 100)

    ocr_data_path = os.path.join(
        OUTPUT_DIR,
        f"page_{page_number + 1}_ocr_data.csv",
    )

    save_dataframe(
        ocr_data_path,
        data,
    )

    print(
        f"✅ Coordinate data saved: "
        f"{ocr_data_path}"
    )

    print()
    print(
        f"Detected words: "
        f"{len(data)}"
    )

    if not data.empty:
        print()
        print(
            "-" * 100
        )
        print(
            "DETECTED WORDS"
        )
        print(
            "-" * 100
        )

        for _, row in data.iterrows():

            text = str(
                row.get(
                    "text",
                    "",
                )
            ).strip()

            if not text:
                continue

            left = int(
                row.get(
                    "left",
                    0,
                )
                or 0
            )

            top = int(
                row.get(
                    "top",
                    0,
                )
                or 0
            )

            word_width = int(
                row.get(
                    "width",
                    0,
                )
                or 0
            )

            word_height = int(
                row.get(
                    "height",
                    0,
                )
                or 0
            )

            try:
                confidence = float(
                    row.get(
                        "conf",
                        0,
                    )
                )
            except Exception:
                confidence = 0.0

            print(
                f"{text:<35} "
                f"X={left:<6} "
                f"Y={top:<6} "
                f"W={word_width:<6} "
                f"H={word_height:<6} "
                f"CONF={confidence:>6.1f}"
            )

    # --------------------------------------------------------
    # DEBUG IMAGE
    # --------------------------------------------------------

    print()
    print("=" * 100)
    print(
        "CREATING DEBUG IMAGE"
    )
    print("=" * 100)

    debug_path = os.path.join(
        OUTPUT_DIR,
        f"page_{page_number + 1}_ocr_debug.png",
    )

    create_debug_image(
        image,
        data,
        debug_path,
    )

    print(
        f"✅ Debug image saved: "
        f"{debug_path}"
    )

    successful_pages += 1


# ============================================================
# CLOSE PDF
# ============================================================

if pdf is not None:
    pdf.close()


# ============================================================
# COMPLETE
# ============================================================

print()
print("=" * 100)
print(
    "OCR / TEXT EXTRACTION COMPLETE"
)
print("=" * 100)
print()
print(
    f"Processed input       : {INPUT_PATH}"
)
print(
    f"Input type            : {INPUT_TYPE}"
)
print(
    f"Total pages/images    : {total_pages}"
)
print(
    f"Successfully processed: {successful_pages}"
)
print(
    f"Output directory      : {OUTPUT_DIR}"
)
