import csv
import json
import re
import sys
from pathlib import Path


# ============================================================
# UNIVERSAL DOCUMENT VALIDATOR
# ============================================================
#
# Purpose:
#   Validate the canonical output produced by invoice_integrator.py.
#
# Design principles:
#   - Observation first: validate what was actually extracted.
#   - No company-specific values, coordinates, or business rules.
#   - Supports one or multiple page-level documents.
#   - Products and charges are validated separately.
#   - Missing/uncertain information becomes REVIEW rather than guessed.
#   - Deterministic arithmetic is used wherever sufficient evidence exists.
#
# Preferred input:
#   final_invoice_data.json
#
# Fallback input:
#   invoice_data.json
#
# Outputs:
#   validation_report.json
#   validation_report.txt
# ============================================================


BASE_DIR = Path(__file__).resolve().parent.parent

if len(sys.argv) > 1:
    WORK_DIR = Path(sys.argv[1])
    if not WORK_DIR.is_absolute():
        WORK_DIR = BASE_DIR / WORK_DIR
else:
    WORK_DIR = BASE_DIR / "output"

WORK_DIR = WORK_DIR.resolve()
WORK_DIR.mkdir(parents=True, exist_ok=True)

FINAL_INPUT_FILE = WORK_DIR / "final_invoice_data.json"
FIELD_INPUT_FILE = WORK_DIR / "invoice_data.json"

OUTPUT_JSON = WORK_DIR / "validation_report.json"
OUTPUT_TEXT = WORK_DIR / "validation_report.txt"

CONFIDENCE_REVIEW_THRESHOLD = 70.0
AMOUNT_TOLERANCE = 0.02


# ============================================================
# START
# ============================================================

print("=" * 75)
print("UNIVERSAL INVOICE / DOCUMENT VALIDATION")
print("=" * 75)
print(f"Working directory : {WORK_DIR}")


# ============================================================
# HELPERS
# ============================================================

def is_present(value):
    if value is None:
        return False

    if isinstance(value, str):
        return bool(value.strip())

    if isinstance(value, (list, tuple, dict)):
        return len(value) > 0

    return True


def clean_text(value):
    if value is None:
        return ""

    if isinstance(value, str):
        return re.sub(r"\s+", " ", value).strip()

    return str(value).strip()


def parse_amount(value):
    if value is None:
        return None

    if isinstance(value, bool):
        return None

    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip()

    if not text:
        return None

    # Keep digits, decimal point and minus sign.
    text = text.replace(",", "")
    match = re.search(r"-?\d+(?:\.\d+)?", text)

    if not match:
        return None

    try:
        return float(match.group(0))
    except ValueError:
        return None


def parse_percentage(value):
    if value is None:
        return None

    if isinstance(value, (int, float)):
        return float(value)

    match = re.search(r"(\d+(?:\.\d+)?)\s*%?", str(value))

    if not match:
        return None

    try:
        return float(match.group(1))
    except ValueError:
        return None


def amounts_match(first, second, tolerance=AMOUNT_TOLERANCE):
    if first is None or second is None:
        return False

    return abs(float(first) - float(second)) <= tolerance


def normalize_tax_type(value):
    text = clean_text(value).upper().replace(" ", "")

    aliases = {
        "CGST+SGST": "CGST/SGST",
        "SGST+CGST": "SGST/CGST",
        "CGSTANDSGST": "CGST/SGST",
        "SGSTANDCGST": "SGST/CGST",
    }

    return aliases.get(text, text)


def valid_pan(value):
    return bool(
        re.fullmatch(
            r"[A-Z]{5}[0-9]{4}[A-Z]",
            clean_text(value).upper()
        )
    )


def valid_gstin(value):
    # Structural GSTIN validation.
    # This intentionally does not hard-code seller/company values.
    return bool(
        re.fullmatch(
            r"[0-9]{2}[A-Z0-9]{13}",
            clean_text(value).upper()
        )
    )


def valid_state_code(value):
    return bool(re.fullmatch(r"\d{2}", clean_text(value)))


def valid_hsn(value):
    return bool(re.fullmatch(r"\d{4,8}", clean_text(value)))


def valid_identifier(value):
    text = clean_text(value)
    return bool(
        re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9./_-]{3,80}",
            text
        )
    )


def first_present(mapping, keys):
    if not isinstance(mapping, dict):
        return None

    for key in keys:
        value = mapping.get(key)
        if is_present(value):
            return value

    return None


def normalize_page(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def safe_list(value):
    return value if isinstance(value, list) else []


def find_page_ocr_confidence(document):
    """
    Recover page-level OCR confidence from the OCR artifact when the
    integrator did not copy the confidence into final_invoice_data.json.

    This is observation-only: no confidence value is invented. Native PDF
    text produced by the OCR stage normally carries confidence 100, while
    Tesseract output carries its measured word confidence.
    """
    page = normalize_page(first_present(document, ("source_page", "page")))
    if page is None:
        return None

    candidates = [
        WORK_DIR / f"page_{page}_ocr_data.csv",
        WORK_DIR / f"page_{page}_ocr_data.csv".lower(),
    ]

    # Also support an OCR subdirectory if a future pipeline stores page
    # artifacts there.
    candidates.extend([
        WORK_DIR / "ocr" / f"page_{page}_ocr_data.csv",
        WORK_DIR / "ocr_data" / f"page_{page}_ocr_data.csv",
    ])

    for path in candidates:
        if not path.exists():
            continue

        values = []
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    raw = row.get("conf")
                    if raw is None:
                        raw = row.get("confidence")
                    parsed = parse_amount(raw)
                    if parsed is not None and 0 <= parsed <= 100:
                        values.append(parsed)
        except (OSError, csv.Error):
            continue

        if values:
            return sum(values) / len(values)

    return None


# ============================================================
# VALIDATION ENGINE
# ============================================================

class Validator:
    def __init__(self):
        self.checks = []

    def add(self, name, status, message, severity="info", document=None):
        self.checks.append({
            "document": document,
            "check": name,
            "status": status,
            "message": message,
            "severity": severity,
        })

    def pass_(self, name, message, document=None):
        self.add(name, "PASS", message, "info", document)

    def review(self, name, message, document=None):
        self.add(name, "REVIEW", message, "warning", document)

    def fail(self, name, message, document=None, severity="error"):
        self.add(name, "FAIL", message, severity, document)

    def summary(self):
        passed = sum(x["status"] == "PASS" for x in self.checks)
        failed = sum(x["status"] == "FAIL" for x in self.checks)
        review = sum(x["status"] == "REVIEW" for x in self.checks)
        warnings = sum(
            x["status"] == "FAIL" and x["severity"] == "warning"
            for x in self.checks
        )
        errors = sum(
            x["status"] == "FAIL" and x["severity"] == "error"
            for x in self.checks
        )

        if errors:
            overall = "FAILED"
        elif review or warnings:
            overall = "NEEDS_REVIEW"
        else:
            overall = "PASSED"

        return {
            "overall_status": overall,
            "total_checks": len(self.checks),
            "passed": passed,
            "failed": failed,
            "review": review,
            "warnings": warnings,
            "errors": errors,
        }


# ============================================================
# INPUT NORMALIZATION
# ============================================================

def load_input():
    if FINAL_INPUT_FILE.exists():
        with open(FINAL_INPUT_FILE, "r", encoding="utf-8") as f:
            return json.load(f), FINAL_INPUT_FILE

    if FIELD_INPUT_FILE.exists():
        with open(FIELD_INPUT_FILE, "r", encoding="utf-8") as f:
            return json.load(f), FIELD_INPUT_FILE

    raise FileNotFoundError(
        "No validation input was found.\n"
        f"Expected:\n  {FINAL_INPUT_FILE}\n"
        f"or:\n  {FIELD_INPUT_FILE}\n\n"
        "Please run invoice_integrator.py first."
    )


def get_documents(data):
    """
    Normalize both the current multi-document integrator structure and
    older single-document structures into a list.

    Current structure:
        {
            "documents": [...],
            ...
        }

    Legacy structure:
        {
            "invoice": {...},
            ...
        }
    """
    if not isinstance(data, dict):
        return []

    documents = data.get("documents")

    if isinstance(documents, list) and documents:
        return documents

    # Single-document compatibility.
    if any(
        key in data
        for key in (
            "invoice",
            "order",
            "seller",
            "billing",
            "shipping",
            "items",
            "charges",
            "totals",
            "payment",
        )
    ):
        return [data]

    return []


def section(document, name):
    value = document.get(name, {})
    return value if isinstance(value, dict) else {}


def get_items(document):
    items = document.get("items", [])
    return safe_list(items)


def get_charges(document):
    charges = document.get("charges", [])
    return safe_list(charges)


def get_observed_total(document):
    totals = section(document, "totals")

    value = first_present(
        totals,
        (
            "total_amount",
            "document_total",
            "grand_total",
            "invoice_total",
        )
    )

    return parse_amount(value)


def get_total_resolution(document):
    totals = section(document, "totals")
    return totals.get("total_resolution")


# ============================================================
# STRUCTURE VALIDATION
# ============================================================

def validate_document_structure(v, document, label):
    required_sections = [
        "invoice",
        "order",
        "seller",
        "billing",
        "shipping",
        "supply_delivery",
        "totals",
        "payment",
    ]

    # items and charges are both optional because a service/fee document
    # can contain charges without products and vice versa.
    for name in required_sections:
        if name in document:
            v.pass_(
                f"{label}.structure.{name}",
                f"{name} section is present.",
                label
            )
        else:
            v.review(
                f"{label}.structure.{name}",
                f"{name} section is unavailable; human review may be required.",
                label
            )

    if "items" in document or "charges" in document:
        v.pass_(
            f"{label}.structure.line_observations",
            "Line-level product/charge observations are available.",
            label
        )
    else:
        v.review(
            f"{label}.structure.line_observations",
            "No product or charge line collection is available.",
            label
        )


# ============================================================
# INVOICE / ORDER
# ============================================================

def validate_invoice(v, document, label):
    invoice = section(document, "invoice")
    order = section(document, "order")

    invoice_number = first_present(
        invoice,
        ("invoice_number", "number", "id")
    )

    invoice_date = first_present(
        invoice,
        ("invoice_date", "date")
    )

    order_number = first_present(
        order,
        ("order_number", "number", "id")
    )

    order_date = first_present(
        order,
        ("order_date", "date")
    )

    if is_present(invoice_number):
        v.pass_(
            f"{label}.invoice.number",
            f"Invoice number found: {invoice_number}",
            label
        )

        if valid_identifier(invoice_number):
            v.pass_(
                f"{label}.invoice.number_structure",
                "Invoice number has a plausible identifier structure.",
                label
            )
        else:
            v.review(
                f"{label}.invoice.number_structure",
                "Invoice number was extracted but its structure is unusual.",
                label
            )
    else:
        v.fail(
            f"{label}.invoice.number",
            "Invoice number is missing.",
            label
        )

    if is_present(invoice_date):
        v.pass_(
            f"{label}.invoice.date",
            f"Invoice date found: {invoice_date}",
            label
        )
    else:
        v.fail(
            f"{label}.invoice.date",
            "Invoice date is missing.",
            label
        )

    if is_present(order_number):
        v.pass_(
            f"{label}.order.number",
            f"Order number found: {order_number}",
            label
        )
    else:
        v.review(
            f"{label}.order.number",
            "Order number is unavailable.",
            label
        )

    if is_present(order_date):
        v.pass_(
            f"{label}.order.date",
            f"Order date found: {order_date}",
            label
        )
    else:
        v.review(
            f"{label}.order.date",
            "Order date is unavailable.",
            label
        )


# ============================================================
# SELLER
# ============================================================

def validate_seller(v, document, label):
    seller = section(document, "seller")

    name = seller.get("name")
    address = seller.get("address")
    pan = seller.get("pan")
    gstin = seller.get("gstin")

    if is_present(name):
        v.pass_(
            f"{label}.seller.name",
            "Seller name is present.",
            label
        )
    else:
        v.fail(
            f"{label}.seller.name",
            "Seller name is missing.",
            label
        )

    if is_present(address):
        v.pass_(
            f"{label}.seller.address",
            "Seller address is present.",
            label
        )
    else:
        v.review(
            f"{label}.seller.address",
            "Seller address is unavailable.",
            label
        )

    if is_present(pan):
        if valid_pan(pan):
            v.pass_(
                f"{label}.seller.pan",
                "PAN has a valid structural format.",
                label
            )
        else:
            v.review(
                f"{label}.seller.pan",
                f"PAN was extracted but has an unusual structure: {pan}",
                label
            )
    else:
        v.review(
            f"{label}.seller.pan",
            "PAN is unavailable.",
            label
        )

    if is_present(gstin):
        if valid_gstin(gstin):
            v.pass_(
                f"{label}.seller.gstin",
                "GSTIN has a valid structural format.",
                label
            )
        else:
            v.review(
                f"{label}.seller.gstin",
                f"GSTIN was extracted but has an unusual structure: {gstin}",
                label
            )
    else:
        v.review(
            f"{label}.seller.gstin",
            "GSTIN is unavailable.",
            label
        )


# ============================================================
# BILLING / SHIPPING
# ============================================================

def validate_party_section(v, document, label, section_name):
    data = section(document, section_name)

    name = data.get("name")
    address = data.get("address")
    state_code = data.get("state_code")

    prefix = f"{label}.{section_name}"

    if is_present(name):
        v.pass_(
            f"{prefix}.name",
            f"{section_name.title()} name is present.",
            label
        )
    else:
        v.review(
            f"{prefix}.name",
            f"{section_name.title()} name is unavailable.",
            label
        )

    if is_present(address):
        v.pass_(
            f"{prefix}.address",
            f"{section_name.title()} address is present.",
            label
        )
    else:
        v.review(
            f"{prefix}.address",
            f"{section_name.title()} address is unavailable.",
            label
        )

    if is_present(state_code):
        if valid_state_code(state_code):
            v.pass_(
                f"{prefix}.state_code",
                f"{section_name.title()} state code has a two-digit structure.",
                label
            )
        else:
            v.review(
                f"{prefix}.state_code",
                f"State code has an unusual structure: {state_code}",
                label
            )
    else:
        v.review(
            f"{prefix}.state_code",
            f"{section_name.title()} state code is unavailable.",
            label
        )


def validate_supply_delivery(v, document, label):
    data = section(document, "supply_delivery")

    supply = data.get("place_of_supply")
    delivery = data.get("place_of_delivery")

    if is_present(supply):
        v.pass_(
            f"{label}.supply_delivery.place_of_supply",
            "Place of supply is present.",
            label
        )
    else:
        v.review(
            f"{label}.supply_delivery.place_of_supply",
            "Place of supply is unavailable.",
            label
        )

    if is_present(delivery):
        v.pass_(
            f"{label}.supply_delivery.place_of_delivery",
            "Place of delivery is present.",
            label
        )
    else:
        v.review(
            f"{label}.supply_delivery.place_of_delivery",
            "Place of delivery is unavailable.",
            label
        )


# ============================================================
# PRODUCT VALIDATION
# ============================================================

def validate_product(v, product, label, index):
    prefix = f"{label}.product_{index}"

    description = product.get("description")
    quantity = parse_amount(
        first_present(product, ("quantity", "qty"))
    )
    unit_price = parse_amount(
        first_present(
            product,
            ("unit_price", "unit_amount", "net_amount", "price")
        )
    )
    net_amount = parse_amount(
        first_present(
            product,
            ("net_amount", "subtotal", "line_net_amount")
        )
    )
    tax_rate = parse_percentage(product.get("tax_rate"))
    tax_amount = parse_amount(
        first_present(product, ("tax_amount", "tax"))
    )
    total_amount = parse_amount(
        first_present(
            product,
            ("total_amount", "line_total", "total")
        )
    )
    tax_type = normalize_tax_type(product.get("tax_type"))
    asin = first_present(product, ("asin", "sku", "item_code"))
    hsn = first_present(product, ("hsn", "hsn_code", "sac"))

    if is_present(description):
        v.pass_(
            f"{prefix}.description",
            "Product description is present.",
            label
        )
    else:
        v.fail(
            f"{prefix}.description",
            "Product description is missing.",
            label
        )

    if quantity is not None and quantity > 0:
        v.pass_(
            f"{prefix}.quantity",
            f"Quantity is valid: {quantity:g}",
            label
        )
    else:
        v.review(
            f"{prefix}.quantity",
            "Quantity is unavailable or not a positive numeric value.",
            label
        )

    if unit_price is not None and unit_price >= 0:
        v.pass_(
            f"{prefix}.unit_price",
            f"Unit/net price is available: {unit_price:.2f}",
            label
        )
    else:
        v.review(
            f"{prefix}.unit_price",
            "Unit/net price is unavailable.",
            label
        )

    if net_amount is not None:
        v.pass_(
            f"{prefix}.net_amount",
            f"Net amount is available: {net_amount:.2f}",
            label
        )

        if quantity is not None and unit_price is not None:
            expected_net = quantity * unit_price

            if amounts_match(expected_net, net_amount):
                v.pass_(
                    f"{prefix}.net_calculation",
                    f"Quantity × unit price = {expected_net:.2f}, matching net amount.",
                    label
                )
            else:
                v.review(
                    f"{prefix}.net_calculation",
                    (
                        f"Quantity × unit price = {expected_net:.2f}, "
                        f"but extracted net amount = {net_amount:.2f}."
                    ),
                    label
                )
    else:
        # If unit price is available, it can still be a useful observed
        # amount, but we do not invent a separate net amount.
        v.review(
            f"{prefix}.net_amount",
            "Net amount is unavailable.",
            label
        )

    if tax_rate is not None and 0 <= tax_rate <= 100:
        v.pass_(
            f"{prefix}.tax_rate",
            f"Tax rate is structurally valid: {tax_rate:g}%",
            label
        )
    else:
        v.review(
            f"{prefix}.tax_rate",
            "Tax rate is unavailable or outside the valid 0–100% range.",
            label
        )

    if is_present(tax_type):
        allowed = {
            "IGST",
            "CGST",
            "SGST",
            "CGST/SGST",
            "SGST/CGST",
            "UTGST",
        }

        if tax_type in allowed:
            v.pass_(
                f"{prefix}.tax_type",
                f"Tax type is recognized: {tax_type}",
                label
            )
        else:
            v.review(
                f"{prefix}.tax_type",
                f"Tax type was extracted but is not in the recognized structural set: {tax_type}",
                label
            )
    else:
        v.review(
            f"{prefix}.tax_type",
            "Tax type is unavailable.",
            label
        )

    if tax_amount is not None:
        v.pass_(
            f"{prefix}.tax_amount",
            f"Tax amount is available: {tax_amount:.2f}",
            label
        )
    else:
        v.review(
            f"{prefix}.tax_amount",
            "Tax amount is unavailable.",
            label
        )

    # Tax arithmetic is performed only when the required observations exist.
    # A combined CGST/SGST observation can contain two tax components while
    # the extracted rate represents the per-component rate. We therefore test
    # both the directly observed rate and the structurally implied combined
    # rate before flagging a review. No company-specific rule is used.
    if tax_rate is not None and tax_amount is not None:
        base = net_amount if net_amount is not None else unit_price

        if base is not None:
            expected_tax = base * tax_rate / 100.0

            if amounts_match(expected_tax, tax_amount):
                v.pass_(
                    f"{prefix}.tax_calculation",
                    (
                        f"Expected tax = {expected_tax:.2f}; "
                        f"extracted tax = {tax_amount:.2f}."
                    ),
                    label
                )
            elif tax_type in {"CGST/SGST", "SGST/CGST"}:
                combined_expected = base * (tax_rate * 2.0) / 100.0

                if amounts_match(combined_expected, tax_amount):
                    v.pass_(
                        f"{prefix}.tax_calculation",
                        (
                            f"Combined CGST/SGST expectation = {combined_expected:.2f}; "
                            f"extracted combined tax = {tax_amount:.2f}. "
                            f"The extracted {tax_rate:g}% rate is treated as a per-component rate."
                        ),
                        label
                    )
                else:
                    v.review(
                        f"{prefix}.tax_calculation",
                        (
                            f"Expected tax = {expected_tax:.2f}; combined expectation = "
                            f"{combined_expected:.2f}; extracted tax = {tax_amount:.2f}. "
                            "The values do not reconcile within tolerance."
                        ),
                        label
                    )
            else:
                v.review(
                    f"{prefix}.tax_calculation",
                    (
                        f"Expected tax = {expected_tax:.2f}; "
                        f"extracted tax = {tax_amount:.2f}. "
                        "The values do not reconcile within tolerance."
                    ),
                    label
                )

    if total_amount is not None:
        v.pass_(
            f"{prefix}.total_amount",
            f"Line total is available: {total_amount:.2f}",
            label
        )
    else:
        v.review(
            f"{prefix}.total_amount",
            "Line total is unavailable.",
            label
        )

    # Deterministic line arithmetic.
    if total_amount is not None and tax_amount is not None:
        base = net_amount if net_amount is not None else unit_price

        if base is not None:
            expected_total = base + tax_amount

            if amounts_match(expected_total, total_amount):
                v.pass_(
                    f"{prefix}.total_calculation",
                    (
                        f"Net + tax = {expected_total:.2f}; "
                        f"line total = {total_amount:.2f}."
                    ),
                    label
                )
            else:
                v.review(
                    f"{prefix}.total_calculation",
                    (
                        f"Net + tax = {expected_total:.2f}; "
                        f"line total = {total_amount:.2f}. "
                        "The values do not reconcile within tolerance."
                    ),
                    label
                )

    if is_present(asin):
        if valid_identifier(asin):
            v.pass_(
                f"{prefix}.identifier",
                f"Line identifier is present: {asin}",
                label
            )
        else:
            v.review(
                f"{prefix}.identifier",
                f"Line identifier has an unusual structure: {asin}",
                label
            )
    else:
        # ASIN/SKU is not universally required for every document.
        v.review(
            f"{prefix}.identifier",
            "No product/item identifier was extracted.",
            label
        )

    if is_present(hsn):
        if valid_hsn(hsn):
            v.pass_(
                f"{prefix}.classification_code",
                f"Classification code has a numeric structure: {hsn}",
                label
            )
        else:
            v.review(
                f"{prefix}.classification_code",
                f"Classification code has an unusual structure: {hsn}",
                label
            )
    else:
        v.review(
            f"{prefix}.classification_code",
            "No HSN/SAC/classification code was extracted.",
            label
        )


# ============================================================
# CHARGE VALIDATION
# ============================================================

def validate_charge(v, charge, label, index):
    prefix = f"{label}.charge_{index}"

    charge_type = first_present(
        charge,
        ("charge_type", "type", "category")
    )
    description = charge.get("description")

    amount = parse_amount(
        first_present(
            charge,
            ("amount", "unit_amount", "net_amount", "base_amount")
        )
    )

    tax_amount = parse_amount(
        first_present(
            charge,
            ("tax_amount", "tax")
        )
    )

    tax_rate = parse_percentage(charge.get("tax_rate"))

    total_amount = parse_amount(
        first_present(
            charge,
            ("total_amount", "total")
        )
    )

    tax_type = normalize_tax_type(charge.get("tax_type"))

    if is_present(charge_type):
        v.pass_(
            f"{prefix}.type",
            f"Charge type is present: {charge_type}",
            label
        )
    else:
        v.review(
            f"{prefix}.type",
            "Charge type is unavailable.",
            label
        )

    if is_present(description):
        v.pass_(
            f"{prefix}.description",
            "Charge description is present.",
            label
        )
    else:
        v.review(
            f"{prefix}.description",
            "Charge description is unavailable.",
            label
        )

    if amount is not None and amount >= 0:
        v.pass_(
            f"{prefix}.amount",
            f"Charge amount is available: {amount:.2f}",
            label
        )
    else:
        v.review(
            f"{prefix}.amount",
            "Charge amount is unavailable.",
            label
        )

    if tax_rate is not None and 0 <= tax_rate <= 100:
        v.pass_(
            f"{prefix}.tax_rate",
            f"Charge tax rate is structurally valid: {tax_rate:g}%",
            label
        )

    if tax_amount is not None:
        v.pass_(
            f"{prefix}.tax_amount",
            f"Charge tax amount is available: {tax_amount:.2f}",
            label
        )

    if is_present(tax_type):
        allowed = {
            "IGST",
            "CGST",
            "SGST",
            "CGST/SGST",
            "SGST/CGST",
            "UTGST",
        }

        if tax_type in allowed:
            v.pass_(
                f"{prefix}.tax_type",
                f"Charge tax type is recognized: {tax_type}",
                label
            )
        else:
            v.review(
                f"{prefix}.tax_type",
                f"Charge tax type is unusual: {tax_type}",
                label
            )

    if (
        amount is not None
        and tax_amount is not None
        and total_amount is not None
    ):
        expected_total = amount + tax_amount

        if amounts_match(expected_total, total_amount):
            v.pass_(
                f"{prefix}.total_calculation",
                (
                    f"Charge amount + tax = {expected_total:.2f}; "
                    f"charge total = {total_amount:.2f}."
                ),
                label
            )
        else:
            v.review(
                f"{prefix}.total_calculation",
                (
                    f"Charge amount + tax = {expected_total:.2f}; "
                    f"charge total = {total_amount:.2f}. "
                    "The values do not reconcile within tolerance."
                ),
                label
            )

    if total_amount is not None:
        v.pass_(
            f"{prefix}.total_amount",
            f"Charge total is available: {total_amount:.2f}",
            label
        )
    else:
        v.review(
            f"{prefix}.total_amount",
            "Charge total is unavailable.",
            label
        )


# ============================================================
# DOCUMENT TOTALS
# ============================================================

def validate_document_totals(v, document, label, products, charges):
    totals = section(document, "totals")

    total_amount = parse_amount(
        first_present(
            totals,
            ("total_amount", "document_total", "grand_total", "invoice_total")
        )
    )

    tax_amount = parse_amount(
        first_present(
            totals,
            ("tax_amount", "total_tax")
        )
    )

    resolution = get_total_resolution(document)

    product_totals = []
    for item in products:
        value = parse_amount(
            first_present(
                item,
                ("total_amount", "line_total", "total")
            )
        )
        if value is not None:
            product_totals.append(value)

    charge_totals = []
    for charge in charges:
        value = parse_amount(
            first_present(
                charge,
                ("total_amount", "total")
            )
        )
        if value is not None:
            charge_totals.append(value)

    if total_amount is not None:
        v.pass_(
            f"{label}.totals.total_present",
            f"Document total is available: {total_amount:.2f}",
            label
        )
    else:
        v.fail(
            f"{label}.totals.total_present",
            "Document total is missing.",
            label
        )

    if tax_amount is not None:
        v.pass_(
            f"{label}.totals.tax_present",
            f"Document tax amount is available: {tax_amount:.2f}",
            label
        )
    else:
        observed_line_tax = []
        for row in products + charges:
            row_tax = parse_amount(first_present(row, ("tax_amount", "tax")))
            if row_tax is not None:
                observed_line_tax.append(row_tax)

        if observed_line_tax:
            v.pass_(
                f"{label}.totals.tax_present",
                (
                    "A separate document-level tax amount is unavailable, "
                    "but line-level tax observations are present: "
                    + ", ".join(f"{value:.2f}" for value in observed_line_tax)
                    + "."
                ),
                label
            )
        else:
            v.review(
                f"{label}.totals.tax_present",
                "Document-level tax amount is unavailable and no line-level tax observation was found.",
                label
            )

    # Product-only document: product totals should reconcile to document total.
    if product_totals and not charge_totals and total_amount is not None:
        product_sum = sum(product_totals)

        if amounts_match(product_sum, total_amount):
            v.pass_(
                f"{label}.totals.product_reconciliation",
                (
                    f"Product totals = {product_sum:.2f}; "
                    f"document total = {total_amount:.2f}."
                ),
                label
            )
        else:
            # The integrator can explicitly record that the document total
            # contains additional document-level observations that are not
            # represented as product rows. In that case the mismatch is an
            # observed structural difference rather than an arithmetic error.
            if resolution == "document_level_observation_with_additional_components":
                difference = total_amount - product_sum
                v.pass_(
                    f"{label}.totals.product_reconciliation",
                    (
                        f"Product totals = {product_sum:.2f}; document total = "
                        f"{total_amount:.2f}. An explicit integrator resolution "
                        f"records additional document-level components totaling "
                        f"{difference:.2f}."
                    ),
                    label
                )
            else:
                v.review(
                    f"{label}.totals.product_reconciliation",
                    (
                        f"Product totals = {product_sum:.2f}; "
                        f"document total = {total_amount:.2f}. "
                        "Additional document-level components may explain the difference."
                    ),
                    label
                )

    # Charge-only document: explicit charge totals should reconcile.
    elif charge_totals and not product_totals and total_amount is not None:
        charge_sum = sum(charge_totals)

        if amounts_match(charge_sum, total_amount):
            v.pass_(
                f"{label}.totals.charge_reconciliation",
                (
                    f"Charge totals = {charge_sum:.2f}; "
                    f"document total = {total_amount:.2f}."
                ),
                label
            )
        else:
            v.review(
                f"{label}.totals.charge_reconciliation",
                (
                    f"Charge totals = {charge_sum:.2f}; "
                    f"document total = {total_amount:.2f}. "
                    "Additional document-level components may exist."
                ),
                label
            )

    # Mixed document: compare only as an observed relationship.
    elif product_totals and charge_totals and total_amount is not None:
        combined = sum(product_totals) + sum(charge_totals)

        if amounts_match(combined, total_amount):
            v.pass_(
                f"{label}.totals.line_reconciliation",
                (
                    f"Product + charge totals = {combined:.2f}; "
                    f"document total = {total_amount:.2f}."
                ),
                label
            )
        else:
            v.review(
                f"{label}.totals.line_reconciliation",
                (
                    f"Product + charge totals = {combined:.2f}; "
                    f"document total = {total_amount:.2f}. "
                    "Additional document-level components may exist."
                ),
                label
            )
    else:
        v.review(
            f"{label}.totals.line_reconciliation",
            "Insufficient line-level totals were available for deterministic reconciliation.",
            label
        )

    if is_present(resolution):
        v.pass_(
            f"{label}.totals.resolution",
            f"Integrator total resolution: {resolution}",
            label
        )


# ============================================================
# PAYMENT
# ============================================================

def validate_payment(v, document, label):
    payment = section(document, "payment")

    transaction_id = first_present(
        payment,
        ("transaction_id", "transaction", "reference")
    )
    mode = payment.get("mode")
    invoice_value = parse_amount(
        first_present(
            payment,
            ("invoice_value", "paid_amount", "amount")
        )
    )

    document_total = get_observed_total(document)

    if is_present(transaction_id):
        v.pass_(
            f"{label}.payment.transaction_id",
            "Payment transaction/reference identifier is present.",
            label
        )
    else:
        v.review(
            f"{label}.payment.transaction_id",
            "Payment transaction/reference identifier is unavailable.",
            label
        )

    if is_present(mode):
        v.pass_(
            f"{label}.payment.mode",
            f"Payment mode is present: {mode}",
            label
        )
    else:
        v.review(
            f"{label}.payment.mode",
            "Payment mode is unavailable.",
            label
        )

    if invoice_value is not None:
        v.pass_(
            f"{label}.payment.invoice_value",
            f"Payment/invoice value is available: {invoice_value:.2f}",
            label
        )

        if document_total is not None:
            if amounts_match(invoice_value, document_total):
                v.pass_(
                    f"{label}.payment.value_reconciliation",
                    (
                        f"Payment/invoice value = {invoice_value:.2f}; "
                        f"document total = {document_total:.2f}."
                    ),
                    label
                )
            else:
                v.review(
                    f"{label}.payment.value_reconciliation",
                    (
                        f"Payment/invoice value = {invoice_value:.2f}; "
                        f"document total = {document_total:.2f}. "
                        "They do not reconcile within tolerance."
                    ),
                    label
                )
    else:
        v.review(
            f"{label}.payment.invoice_value",
            "Payment/invoice value is unavailable.",
            label
        )


# ============================================================
# CONFIDENCE / SOURCE QUALITY
# ============================================================

def validate_confidence(v, document, label):
    """
    Look for confidence values only when they are actually present in the
    integrated document. We do not invent confidence.

    Supported shapes:
        document["confidence"]
        document["ocr_confidence"]
        document["processing"]["ocr_confidence"]
    """
    candidates = [
        document.get("confidence"),
        document.get("ocr_confidence"),
        section(document, "processing").get("ocr_confidence"),
    ]

    value = None

    for candidate in candidates:
        parsed = parse_amount(candidate)
        if parsed is not None:
            value = parsed
            break

    source = "integrated document"

    if value is None:
        value = find_page_ocr_confidence(document)
        source = "page OCR artifact"

    if value is None:
        v.review(
            f"{label}.ocr_confidence",
            "Document-level OCR confidence information is unavailable.",
            label
        )
        return

    if 0 <= value <= 100:
        if value >= CONFIDENCE_REVIEW_THRESHOLD:
            v.pass_(
                f"{label}.ocr_confidence",
                f"OCR confidence = {value:.2f} ({source}).",
                label
            )
        else:
            v.review(
                f"{label}.ocr_confidence",
                (
                    f"OCR confidence = {value:.2f} ({source}), below the "
                    f"{CONFIDENCE_REVIEW_THRESHOLD:.0f} review threshold."
                ),
                label
            )
    else:
        v.review(
            f"{label}.ocr_confidence",
            f"OCR confidence value is outside 0–100: {value}",
            label
        )


# ============================================================
# DOCUMENT VALIDATION
# ============================================================

def validate_document(v, document, index):
    page = normalize_page(
        first_present(
            document,
            ("source_page", "page")
        )
    )

    if page is not None:
        label = f"document_{index}_page_{page}"
    else:
        label = f"document_{index}"

    print("\n" + "-" * 75)
    print(f"VALIDATING {label}")
    print("-" * 75)

    v.pass_(
        f"{label}.identity",
        (
            f"Document {index}"
            + (f" is associated with source page {page}." if page else ".")
        ),
        label
    )

    validate_document_structure(v, document, label)
    validate_invoice(v, document, label)
    validate_seller(v, document, label)
    validate_party_section(v, document, label, "billing")
    validate_party_section(v, document, label, "shipping")
    validate_supply_delivery(v, document, label)

    products = get_items(document)
    charges = get_charges(document)

    if products:
        v.pass_(
            f"{label}.products.present",
            f"{len(products)} product row(s) are available.",
            label
        )

        for index, product in enumerate(products, start=1):
            validate_product(v, product, label, index)
    elif charges:
        v.pass_(
            f"{label}.products.present",
            (
                "No product rows are present; the document contains "
                f"{len(charges)} charge row(s), so it is treated as a charge-only document."
            ),
            label
        )
    else:
        v.review(
            f"{label}.products.present",
            "No product or charge rows were extracted for this document.",
            label
        )

    if charges:
        v.pass_(
            f"{label}.charges.present",
            f"{len(charges)} charge row(s) are available.",
            label
        )

        for index, charge in enumerate(charges, start=1):
            validate_charge(v, charge, label, index)
    else:
        v.pass_(
            f"{label}.charges.present",
            "No charge rows were extracted for this document.",
            label
        )

    validate_document_totals(
        v,
        document,
        label,
        products,
        charges
    )

    validate_payment(v, document, label)
    validate_confidence(v, document, label)

    return label


# ============================================================
# RUN
# ============================================================

data, input_file = load_input()

documents = get_documents(data)

if not documents:
    raise ValueError(
        "The validation input does not contain a recognizable document structure."
    )

print(f"Input file         : {input_file}")
print(f"Documents detected : {len(documents)}")

validator = Validator()

for index, document in enumerate(documents, start=1):
    validate_document(
        validator,
        document,
        index
    )


# ============================================================
# BUILD REPORT
# ============================================================

summary = validator.summary()

validation_report = {
    "overall_status": summary["overall_status"],
    "summary": {
        "total_documents": len(documents),
        "total_checks": summary["total_checks"],
        "passed": summary["passed"],
        "failed": summary["failed"],
        "review": summary["review"],
        "warnings": summary["warnings"],
        "errors": summary["errors"],
    },
    "input_file": str(input_file),
    "working_directory": str(WORK_DIR),
    "rules": {
        "amount_tolerance": AMOUNT_TOLERANCE,
        "confidence_review_threshold": CONFIDENCE_REVIEW_THRESHOLD,
        "principle": (
            "Validate observed evidence; do not guess missing or uncertain values."
        ),
    },
    "checks": validator.checks,
}


# ============================================================
# SAVE JSON
# ============================================================

with open(
    OUTPUT_JSON,
    "w",
    encoding="utf-8"
) as f:
    json.dump(
        validation_report,
        f,
        indent=4,
        ensure_ascii=False
    )


# ============================================================
# SAVE HUMAN-READABLE REPORT
# ============================================================

with open(
    OUTPUT_TEXT,
    "w",
    encoding="utf-8"
) as f:

    f.write("UNIVERSAL DOCUMENT VALIDATION REPORT\n")
    f.write("=" * 75 + "\n\n")

    f.write(
        f"OVERALL STATUS: {summary['overall_status']}\n\n"
    )

    f.write(
        f"Documents     : {len(documents)}\n"
        f"Total checks  : {summary['total_checks']}\n"
        f"Passed        : {summary['passed']}\n"
        f"Failed        : {summary['failed']}\n"
        f"Review        : {summary['review']}\n"
        f"Warnings      : {summary['warnings']}\n"
        f"Errors        : {summary['errors']}\n\n"
    )

    f.write("=" * 75 + "\n")
    f.write("DETAILS\n")
    f.write("=" * 75 + "\n\n")

    current_document = None

    for check in validator.checks:
        document_name = check.get("document")

        if document_name != current_document:
            current_document = document_name
            f.write(
                f"\n[{current_document}]\n"
                + "-" * 75
                + "\n"
            )

        f.write(
            f"[{check['status']}] "
            f"{check['check']}\n"
        )
        f.write(
            f"    {check['message']}\n\n"
        )

    f.write("=" * 75 + "\n")
    f.write("VALIDATION COMPLETE\n")


# ============================================================
# DISPLAY SUMMARY
# ============================================================

print("\n" + "=" * 75)
print("VALIDATION SUMMARY")
print("=" * 75)

print(f"\nOverall Status : {summary['overall_status']}")
print(f"Documents      : {len(documents)}")
print(f"Total Checks   : {summary['total_checks']}")
print(f"Passed         : {summary['passed']}")
print(f"Failed         : {summary['failed']}")
print(f"Review         : {summary['review']}")
print(f"Warnings       : {summary['warnings']}")
print(f"Errors         : {summary['errors']}")


# ============================================================
# ATTENTION ITEMS
# ============================================================

attention = [
    check
    for check in validator.checks
    if check["status"] in {"FAIL", "REVIEW"}
]

print("\n" + "=" * 75)
print("ITEMS REQUIRING ATTENTION")
print("=" * 75)

if not attention:
    print("\nNo validation issues found.")
else:
    for check in attention:
        print(
            f"\n[{check['status']}] "
            f"{check['check']}"
        )
        print(f"    {check['message']}")


# ============================================================
# COMPLETE
# ============================================================

print("\n" + "=" * 75)
print("VALIDATION COMPLETE")
print("=" * 75)
print(f"\nSaved JSON   : {OUTPUT_JSON}")
print(f"Saved report : {OUTPUT_TEXT}")
