import csv
import json
import sys
from pathlib import Path
from copy import deepcopy


# ============================================================
# PATHS
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_WORK_DIR = BASE_DIR / "output"

if len(sys.argv) > 1:
    WORK_DIR = Path(sys.argv[1])
    if not WORK_DIR.is_absolute():
        WORK_DIR = (BASE_DIR / WORK_DIR).resolve()
else:
    WORK_DIR = DEFAULT_WORK_DIR

WORK_DIR.mkdir(parents=True, exist_ok=True)

FIELD_DATA_FILE = WORK_DIR / "invoice_data.json"
TABLE_DATA_FILE = WORK_DIR / "extracted_items.csv"
CLASSIFICATION_FILE = WORK_DIR / "document_type.json"
OUTPUT_FILE = WORK_DIR / "final_invoice_data.json"


# ============================================================
# HELPERS
# ============================================================

NULL_STRINGS = {
    "",
    "none",
    "null",
    "nan",
    "n/a",
    "na",
    "unknown",
}


def clean_value(value):
    """Return None for empty/null-like values and stripped text otherwise."""
    if value is None:
        return None

    text = str(value).strip()

    if text.lower() in NULL_STRINGS:
        return None

    return text


def parse_number(value):
    """
    Convert a CSV numeric value to int/float when possible.

    Currency symbols and thousands separators are removed only for
    numeric conversion. The original textual observation remains
    available in the source row.
    """
    value = clean_value(value)

    if value is None:
        return None

    text = (
        value.replace(",", "")
        .replace("₹", "")
        .replace("$", "")
        .replace("€", "")
        .replace("£", "")
        .strip()
    )

    try:
        number = float(text)
    except (TypeError, ValueError):
        return value

    if number.is_integer():
        return int(number)

    return number


def normalize_page(value):
    value = clean_value(value)

    if value is None:
        return None

    try:
        number = int(float(value))
        return number
    except (TypeError, ValueError):
        return value


def normalize_row_type(value):
    value = clean_value(value)

    if value is None:
        return None

    return value.lower()


def first_value(row, *names):
    """Return the first populated CSV column among the supplied names."""
    for name in names:
        if name in row:
            value = clean_value(row.get(name))
            if value is not None:
                return value
    return None


def detect_classification(work_dir):
    """
    Read document_type.json when available.

    The integrator does not assume a particular company or invoice
    provider. If classification is unavailable, retain a neutral
    document-type value.
    """
    if not CLASSIFICATION_FILE.exists():
        return None

    try:
        with open(
            CLASSIFICATION_FILE,
            "r",
            encoding="utf-8",
        ) as handle:
            data = json.load(handle)

        if isinstance(data, dict):
            for key in (
                "document_type",
                "classification",
                "type",
                "category",
            ):
                value = clean_value(data.get(key))
                if value:
                    return value

    except (OSError, json.JSONDecodeError):
        pass

    return None


def load_field_data():
    with open(
        FIELD_DATA_FILE,
        "r",
        encoding="utf-8",
    ) as handle:
        data = json.load(handle)

    if not isinstance(data, dict):
        raise ValueError(
            "invoice_data.json must contain a JSON object."
        )

    return data


def load_table_rows():
    rows = []

    with open(
        TABLE_DATA_FILE,
        "r",
        encoding="utf-8",
        newline="",
    ) as handle:
        reader = csv.DictReader(handle)

        for raw_row in reader:
            row = {
                str(key).strip(): value
                for key, value in raw_row.items()
                if key is not None
            }

            rows.append(row)

    return rows


# ============================================================
# TABLE ROW NORMALIZATION
# ============================================================

def normalize_table_row(row):
    """
    Convert one extracted_items.csv row into a stable structured
    representation without changing the observed business meaning.

    The function accepts both the current table extractor column names
    and common legacy aliases.
    """
    page = normalize_page(
        first_value(
            row,
            "page_number",
            "page",
            "source_page",
        )
    )

    row_type = normalize_row_type(
        first_value(
            row,
            "row_type",
            "type",
            "item_type",
        )
    )

    charge_type = first_value(
        row,
        "charge_type",
        "charge",
        "charge_category",
    )

    normalized = {
        "page": page,
        "type": row_type,
        "charge_type": charge_type,
        "description": first_value(
            row,
            "description",
            "product_description",
            "item_description",
        ),
        "quantity": parse_number(
            first_value(
                row,
                "quantity",
                "qty",
            )
        ),
        "unit_price": parse_number(
            first_value(
                row,
                "unit_price",
                "unit_amount",
                "price",
            )
        ),
        "discount": parse_number(
            first_value(
                row,
                "discount",
                "discount_amount",
            )
        ),
        "net_amount": parse_number(
            first_value(
                row,
                "net_amount",
                "net",
                "amount_before_tax",
            )
        ),
        "tax_rate": first_value(
            row,
            "tax_rate",
            "tax_percentage",
        ),
        "tax_type": first_value(
            row,
            "tax_type",
            "tax_component",
        ),
        "tax_amount": parse_number(
            first_value(
                row,
                "tax_amount",
                "tax",
            )
        ),
        "total_amount": parse_number(
            first_value(
                row,
                "total_amount",
                "total",
            )
        ),
        "asin": first_value(
            row,
            "asin",
            "product_code",
            "item_code",
        ),
        "hsn": first_value(
            row,
            "hsn",
            "hsn_code",
            "sac",
            "service_code",
        ),
        "validation": first_value(
            row,
            "validation",
            "status",
            "validation_status",
        ),
    }

    # Keep additional source columns so integration never discards
    # observations introduced by a future extractor version.
    reserved = {
        "page_number",
        "page",
        "source_page",
        "row_type",
        "type",
        "item_type",
        "charge_type",
        "charge",
        "charge_category",
        "description",
        "product_description",
        "item_description",
        "quantity",
        "qty",
        "unit_price",
        "unit_amount",
        "price",
        "discount",
        "discount_amount",
        "net_amount",
        "net",
        "amount_before_tax",
        "tax_rate",
        "tax_percentage",
        "tax_type",
        "tax_component",
        "tax_amount",
        "tax",
        "total_amount",
        "total",
        "asin",
        "product_code",
        "item_code",
        "hsn",
        "hsn_code",
        "sac",
        "service_code",
        "validation",
        "status",
        "validation_status",
    }

    observations = {}

    for key, value in row.items():
        if key in reserved:
            continue

        cleaned = clean_value(value)
        if cleaned is not None:
            observations[key] = cleaned

    if observations:
        normalized["source_observations"] = observations

    return normalized


# ============================================================
# PAGE-AWARE DOCUMENT HANDLING
# ============================================================

def get_field_documents(field_data):
    """
    Normalize the field extractor's page-aware output.

    Current field_extractor.py produces documents[] even for one-page
    documents. Legacy single-document JSON is also supported.
    """
    documents = field_data.get("documents")

    if isinstance(documents, list) and documents:
        normalized = []

        for index, document in enumerate(documents, start=1):
            if not isinstance(document, dict):
                continue

            copy = deepcopy(document)

            if copy.get("page") is None:
                copy["page"] = index

            normalized.append(copy)

        if normalized:
            return normalized

    # Legacy single-document fallback.
    return [
        {
            "page": 1,
            "invoice": field_data.get("invoice", {}),
            "order": field_data.get("order", {}),
            "seller": field_data.get("seller", {}),
            "billing": field_data.get("billing", {}),
            "shipping": field_data.get("shipping", {}),
            "supply_delivery": field_data.get(
                "supply_delivery",
                {},
            ),
            "items": field_data.get("items", []),
            "totals": field_data.get("totals", {}),
            "amount_in_words": field_data.get(
                "amount_in_words",
                field_data.get("totals", {}).get(
                    "amount_in_words",
                    "",
                )
                if isinstance(field_data.get("totals"), dict)
                else "",
            ),
            "reverse_charge": field_data.get(
                "reverse_charge",
                "",
            ),
            "payment": field_data.get("payment", {}),
            "source_observation_count": field_data.get(
                "source_observation_count"
            ),
        }
    ]


def attach_table_rows(documents, table_rows):
    """
    Attach table rows to the field-extractor document with the same page.

    This is the critical integration rule for multi-page input:
    page 1 rows never become page 2 rows merely because both belong
    to the same PDF.
    """
    by_page = {}

    for row in table_rows:
        page = row.get("page")
        by_page.setdefault(page, []).append(row)

    for document in documents:
        page = normalize_page(document.get("page"))

        page_rows = by_page.get(page, [])

        # If a table extractor has no page column and there is exactly
        # one document, all rows belong to that document.
        if not page_rows and len(documents) == 1 and None in by_page:
            page_rows = by_page.get(None, [])

        products = []
        charges = []
        other_rows = []

        for row in page_rows:
            row_type = row.get("type")

            if row_type == "product":
                products.append(row)

            elif row_type in {
                "charge",
                "fee",
                "service",
            }:
                charges.append(row)

            else:
                other_rows.append(row)

        # Table extraction is authoritative for product/charge rows.
        # Keep field-extractor items separately for traceability when
        # they contain observations not represented by the table.
        field_items = document.get("items", [])
        if not isinstance(field_items, list):
            field_items = []

        document["items"] = products
        document["charges"] = charges
        document["table_other_rows"] = other_rows

        if field_items:
            document["field_extractor_items"] = field_items

        document["table_row_count"] = len(page_rows)
        document["table_product_count"] = len(products)
        document["table_charge_count"] = len(charges)

        # Resolve the document total only after product/charge roles have
        # been attached, because those semantic roles are needed to avoid
        # mistaking a tax component for the document total.
        resolve_document_total(document)


# ============================================================
# FINANCIAL TOTAL RESOLUTION
# ============================================================

def resolve_document_total(document):
    """
    Resolve the document-level total without overwriting the original
    field-extractor observation blindly.

    The field extractor may capture a nearby tax amount as the apparent
    total when a document contains a charge row. Table extraction gives
    us a stronger semantic role for product/charge totals.

    Rules are observation-first and generic:
      1. Preserve the original field-extracted total as observed_total_amount.
      2. If the page contains only charge rows with explicit totals, use
         the sum of those charge totals as the resolved document total.
      3. If product rows have explicit totals, use their sum when it agrees
         with the observed document total; otherwise retain the observed
         total because it may include shipping/other document-level charges.
      4. If there are no usable row totals, retain the observed total.

    No company, product, coordinate, or fixed-value rule is used.
    """
    totals = document.get("totals")
    if not isinstance(totals, dict):
        totals = {}
        document["totals"] = totals

    observed = parse_number(totals.get("total_amount"))
    if observed is not None:
        totals["observed_total_amount"] = observed

    products = document.get("items", [])
    charges = document.get("charges", [])

    product_totals = []
    for item in products if isinstance(products, list) else []:
        value = parse_number(item.get("total_amount"))
        if isinstance(value, (int, float)):
            product_totals.append(float(value))

    charge_totals = []
    for charge in charges if isinstance(charges, list) else []:
        value = parse_number(charge.get("total_amount"))
        if isinstance(value, (int, float)):
            charge_totals.append(float(value))

    resolved = observed
    resolution = "field_extractor_observation"

    if not product_totals and charge_totals:
        resolved = round(sum(charge_totals), 2)
        resolution = "sum_of_charge_totals"

    elif product_totals:
        product_sum = round(sum(product_totals), 2)

        if observed is not None and abs(float(observed) - product_sum) <= 0.02:
            resolved = product_sum
            resolution = "validated_product_total_sum"
        elif observed is None:
            resolved = product_sum
            resolution = "sum_of_product_totals"
        else:
            # A document-level total may legitimately include shipping,
            # discounts, or other charges not represented as product rows.
            resolved = observed
            resolution = "document_level_observation_with_additional_components"

    if resolved is not None:
        if isinstance(resolved, float) and resolved.is_integer():
            resolved = int(resolved)
        totals["total_amount"] = resolved

    totals["total_resolution"] = resolution


# ============================================================
# OUTPUT CONSTRUCTION
# ============================================================

def build_final_output(
    field_data,
    documents,
    classification,
):
    """
    Build the canonical final JSON.

    Multi-page documents remain page-aware. A PDF containing two
    separate invoices therefore produces two document records instead
    of one merged seller/invoice object.
    """
    source_pages = [
        document.get("page")
        for document in documents
    ]

    result = {
        "document_count": len(documents),
        "source_pages": source_pages,
        "documents": documents,
        "processing": {
            "stage": "invoice_integration",
            "inputs": [
                "invoice_data.json",
                "extracted_items.csv",
            ],
            "document_type": classification or "INVOICE",
            "page_aware": True,
            "principle": (
                "Preserve observed page-level records and do not "
                "merge separate invoices or sellers."
            ),
        },
    }

    # Preserve selected metadata from field extraction if present.
    for key in (
        "source_file",
        "source_pages",
    ):
        if key in field_data and key not in result:
            result[key] = field_data[key]

    # Backward-compatible single-document view.
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
                "charges": document.get("charges", []),
                "totals": document.get("totals", {}),
                "amount_in_words": document.get(
                    "amount_in_words",
                    "",
                ),
                "reverse_charge": document.get(
                    "reverse_charge",
                    "",
                ),
                "payment": document.get(
                    "payment",
                    {},
                ),
            }
        )

    return result


# ============================================================
# DISPLAY
# ============================================================

def display_document_summary(document, index):
    invoice = document.get("invoice", {})
    order = document.get("order", {})
    seller = document.get("seller", {})
    totals = document.get("totals", {})

    print(f"\nDOCUMENT {index}")
    print(f"  Source page    : {document.get('page')}")
    print(
        f"  Invoice number : "
        f"{invoice.get('invoice_number')}"
    )
    print(
        f"  Invoice date   : "
        f"{invoice.get('invoice_date')}"
    )
    print(
        f"  Order number   : "
        f"{order.get('order_number')}"
    )
    print(
        f"  Seller         : "
        f"{seller.get('name')}"
    )
    print(
        f"  Document total : "
        f"{totals.get('total_amount')}"
    )
    print(
        f"  Products       : "
        f"{document.get('table_product_count', 0)}"
    )
    print(
        f"  Charges        : "
        f"{document.get('table_charge_count', 0)}"
    )


def display_rows(document):
    products = document.get("items", [])
    charges = document.get("charges", [])

    if products:
        print("  Product rows:")

        for index, item in enumerate(products, start=1):
            print(f"    Product {index}")
            print(
                f"      Description : "
                f"{item.get('description')}"
            )
            print(
                f"      Quantity    : "
                f"{item.get('quantity')}"
            )
            print(
                f"      Net Amount  : "
                f"{item.get('net_amount')}"
            )
            print(
                f"      Tax         : "
                f"{item.get('tax_amount')}"
            )
            print(
                f"      Total       : "
                f"{item.get('total_amount')}"
            )
            print(
                f"      ASIN        : "
                f"{item.get('asin')}"
            )
            print(
                f"      HSN         : "
                f"{item.get('hsn')}"
            )
            print(
                f"      Validation  : "
                f"{item.get('validation')}"
            )

    if charges:
        print("  Charge rows:")

        for index, charge in enumerate(charges, start=1):
            print(f"    Charge {index}")
            print(
                f"      Type        : "
                f"{charge.get('charge_type')}"
            )
            print(
                f"      Description : "
                f"{charge.get('description')}"
            )
            print(
                f"      Amount      : "
                f"{charge.get('net_amount')}"
            )
            print(
                f"      Tax         : "
                f"{charge.get('tax_amount')}"
            )
            print(
                f"      Total       : "
                f"{charge.get('total_amount')}"
            )


# ============================================================
# MAIN
# ============================================================

def main():
    print("=" * 75)
    print("UNIVERSAL INVOICE INTEGRATION")
    print("=" * 75)

    print(f"\nWorking directory : {WORK_DIR}")
    print(f"Field data        : {FIELD_DATA_FILE}")
    print(f"Table data        : {TABLE_DATA_FILE}")
    print(f"Output file       : {OUTPUT_FILE}")

    if not FIELD_DATA_FILE.exists():
        raise FileNotFoundError(
            f"\nField extraction file not found:\n"
            f"{FIELD_DATA_FILE}\n\n"
            f"Run field_extractor.py first."
        )

    if not TABLE_DATA_FILE.exists():
        raise FileNotFoundError(
            f"\nTable extraction file not found:\n"
            f"{TABLE_DATA_FILE}\n\n"
            f"Run table_extractor.py first."
        )

    print("\nLoading field extraction...")
    field_data = load_field_data()

    print("Field data loaded.")

    print("\nLoading table extraction...")
    raw_table_rows = load_table_rows()

    print(
        f"Table rows loaded : "
        f"{len(raw_table_rows)}"
    )

    table_rows = [
        normalize_table_row(row)
        for row in raw_table_rows
    ]

    documents = get_field_documents(field_data)

    print(
        f"Field documents detected : "
        f"{len(documents)}"
    )

    attach_table_rows(
        documents,
        table_rows,
    )

    classification = detect_classification(WORK_DIR)

    final_data = build_final_output(
        field_data,
        documents,
        classification,
    )

    with open(
        OUTPUT_FILE,
        "w",
        encoding="utf-8",
    ) as handle:
        json.dump(
            final_data,
            handle,
            indent=4,
            ensure_ascii=False,
        )

    print("\n" + "=" * 75)
    print("INTEGRATED DOCUMENTS")
    print("=" * 75)

    for index, document in enumerate(
        documents,
        start=1,
    ):
        display_document_summary(
            document,
            index,
        )
        display_rows(document)

    print("\n" + "=" * 75)
    print("INTEGRATION COMPLETE")
    print("=" * 75)

    print(
        f"\nDocuments integrated : "
        f"{len(documents)}"
    )
    print(
        f"Table rows integrated : "
        f"{len(table_rows)}"
    )
    print(
        f"Output : "
        f"{OUTPUT_FILE}"
    )


if __name__ == "__main__":
    main()
