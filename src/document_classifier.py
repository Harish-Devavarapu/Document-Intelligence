import sys
import json
import re
from pathlib import Path


# ============================================================
# UNIVERSAL DOCUMENT CLASSIFIER
# ============================================================
#
# Purpose:
#   Classify a document from OCR observations without depending on
#   a specific company, seller, product, invoice template, or layout.
#
# Supported broad document families:
#   INVOICE
#   RECEIPT
#   GENERAL_DOCUMENT
#   UNKNOWN
#
# Design principles:
#   - Observation-first
#   - No Amazon/company-specific classification
#   - No hard-coded document values or coordinates
#   - Analyze all OCR pages, not only page 1
#   - Conservative classification when evidence is ambiguous
# ============================================================


def normalize(text):
    """Normalize OCR text for classification."""
    text = str(text or "").upper()
    text = re.sub(r"\s+", " ", text)
    return text.strip()


# ============================================================
# GENERIC DOCUMENT EVIDENCE
# ============================================================

INVOICE_PATTERNS = [
    (r"\bTAX\s+INVOICE\b", 5),
    (r"\bINVOICE\b", 4),
    (r"\bINVOICE\s+(?:NO|NUMBER|DATE|DETAILS)\b", 4),
    (r"\bBILLING\s+ADDRESS\b", 2),
    (r"\bSHIPPING\s+ADDRESS\b", 2),
    (r"\bORDER\s+(?:NO|NUMBER|ID|DATE)\b", 3),
    (r"\bGST(?:IN)?\b", 2),
    (r"\bGST\s+REGISTRATION\b", 3),
    (r"\bHSN\b", 2),
    (r"\bSAC\b", 2),
    (r"\bPLACE\s+OF\s+(?:SUPPLY|DELIVERY)\b", 3),
    (r"\bAMOUNT\s+IN\s+WORDS\b", 3),
    (r"\bREVERSE\s+CHARGE\b", 2),
    (r"\bTOTAL\b", 1),
]

RECEIPT_PATTERNS = [
    (r"\bRECEIPT\b", 4),
    (r"\bCASH\s+RECEIPT\b", 5),
    (r"\bCASH\s+MEMO\b", 4),
    (r"\bSUBTOTAL\b", 2),
    (r"\bCHANGE\b", 2),
    (r"\bTENDER\b", 2),
    (r"\bPAYMENT\s+METHOD\b", 2),
    (r"\bMODE\s+OF\s+PAYMENT\b", 2),
    (r"\bQTY\b", 1),
    (r"\bQUANTITY\b", 1),
    (r"\bTOTAL\b", 1),
]

GENERAL_PATTERNS = [
    (r"\bNAME\b", 1),
    (r"\bADDRESS\b", 1),
    (r"\bPHONE\b", 1),
    (r"\bEMAIL\b", 1),
    (r"\bDATE\s+OF\s+BIRTH\b", 2),
    (r"\bDESCRIPTION\b", 1),
    (r"\bREFERENCE\b", 1),
]


def score_patterns(text, patterns):
    """
    Score structural evidence.

    Each rule contributes at most once, even if the OCR text contains
    the same term many times.
    """
    score = 0
    evidence = []

    for pattern, weight in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            score += weight
            evidence.append(pattern)

    return score, evidence


def classify_document(text):
    """
    Classify a document using generic structural observations.

    Company names are deliberately not used as document categories.
    For example, an Amazon invoice and a non-Amazon invoice are both
    classified as INVOICE when their structure supports that result.
    """
    text = normalize(text)

    invoice_score, invoice_evidence = score_patterns(
        text,
        INVOICE_PATTERNS,
    )

    receipt_score, receipt_evidence = score_patterns(
        text,
        RECEIPT_PATTERNS,
    )

    general_score, general_evidence = score_patterns(
        text,
        GENERAL_PATTERNS,
    )

    scores = {
        "INVOICE": invoice_score,
        "RECEIPT": receipt_score,
        "GENERAL_DOCUMENT": general_score,
    }

    evidence = {
        "INVOICE": invoice_evidence,
        "RECEIPT": receipt_evidence,
        "GENERAL_DOCUMENT": general_evidence,
    }

    ranked = sorted(
        scores.items(),
        key=lambda item: item[1],
        reverse=True,
    )

    best_type, best_score = ranked[0]
    second_score = ranked[1][1]

    # No meaningful structural evidence.
    if best_score == 0:
        return "UNKNOWN", 0, scores, evidence

    # Do not force a classification when the strongest candidates tie.
    if best_score == second_score:
        return "UNKNOWN", 0, scores, evidence

    # Conservative heuristic confidence.
    separation = best_score - second_score

    if best_score >= 12 and separation >= 5:
        confidence = 95
    elif best_score >= 9 and separation >= 4:
        confidence = 90
    elif best_score >= 6 and separation >= 3:
        confidence = 80
    elif best_score >= 4 and separation >= 2:
        confidence = 70
    else:
        confidence = 55

    return best_type, confidence, scores, evidence


# ============================================================
# OCR PAGE DISCOVERY
# ============================================================

def find_ocr_files(work_dir):
    """
    Find all page_N_ocr.txt files.

    The classifier intentionally reads every page so a multi-page
    document is not classified from page 1 alone.
    """
    page_files = []

    for file in work_dir.glob("page_*_ocr.txt"):
        match = re.match(r"^page_(\d+)_ocr\.txt$", file.name, re.IGNORECASE)

        if match:
            page_number = int(match.group(1))
            page_files.append((page_number, file))

    page_files.sort(key=lambda item: item[0])

    # Backward compatibility.
    if not page_files:
        legacy_file = work_dir / "page_1_ocr.txt"

        if legacy_file.exists():
            page_files.append((1, legacy_file))

    return page_files


def read_all_ocr(work_dir):
    """
    Read all OCR pages and return:
        combined_text
        page_information
    """
    page_files = find_ocr_files(work_dir)

    if not page_files:
        raise FileNotFoundError(
            f"No OCR text files found in:\n{work_dir}\n\n"
            "Expected files such as page_1_ocr.txt, page_2_ocr.txt, ..."
        )

    combined_parts = []
    page_information = []

    for page_number, ocr_file in page_files:
        text = ocr_file.read_text(
            encoding="utf-8",
            errors="ignore",
        )

        normalized = normalize(text)

        combined_parts.append(normalized)

        page_information.append(
            {
                "page": page_number,
                "file": ocr_file.name,
                "text_length": len(text),
            }
        )

    combined_text = " ".join(combined_parts)

    return combined_text, page_information


# ============================================================
# MAIN
# ============================================================

def main():
    if len(sys.argv) > 1:
        work_dir = Path(sys.argv[1])
    else:
        work_dir = Path("output")

    if not work_dir.exists():
        raise FileNotFoundError(
            f"Working directory not found:\n{work_dir}"
        )

    combined_text, pages = read_all_ocr(work_dir)

    doc_type, confidence, scores, evidence = classify_document(
        combined_text
    )

    result = {
        "document_type": doc_type,
        "confidence": confidence,
        "confidence_type": "heuristic_structural_score",
        "scores": scores,
        "evidence": evidence,
        "pages_analyzed": pages,
        "classification_note": (
            "Classification is based on generic OCR/document structure. "
            "Company, seller, product, invoice number, and coordinate "
            "values are not used as document-specific classification rules."
        ),
    }

    output_file = work_dir / "document_type.json"

    with output_file.open("w", encoding="utf-8") as f:
        json.dump(
            result,
            f,
            indent=4,
            ensure_ascii=False,
        )

    print("\n" + "=" * 70)
    print("UNIVERSAL DOCUMENT TYPE DETECTION")
    print("=" * 70)

    print(f"\nWorking directory : {work_dir}")
    print(f"Pages analyzed    : {len(pages)}")
    print(f"Document Type     : {doc_type}")
    print(f"Confidence        : {confidence}%")

    print("\nScores")

    for key, value in scores.items():
        print(f"{key:<22} {value}")

    print("\nEvidence")

    for key, patterns in evidence.items():
        print(f"{key:<22} {len(patterns)} matched rules")

    print("\nSaved:")
    print(output_file)


if __name__ == "__main__":
    main()
