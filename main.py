import sys
import os
import shutil
import subprocess
from pathlib import Path


# ============================================================
# PROJECT PATHS
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
INPUT_DIR = PROJECT_ROOT / "input"
OUTPUT_DIR = PROJECT_ROOT / "output"


# ============================================================
# SUPPORTED FILE TYPES
# ============================================================

SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".bmp",
    ".tif",
    ".tiff",
    ".webp",
}


# ============================================================
# AMAZON INVOICE PIPELINE
#
# Only Amazon invoices use the detailed structured invoice
# extraction pipeline. Other documents, including invoices from
# other companies, remain on the generic OCR route.
# ============================================================

INVOICE_PIPELINE = [
    ("Layout Analysis", SRC_DIR / "layout_analysis.py"),
    ("Spatial Parsing", SRC_DIR / "spatial_parser.py"),
    ("Field Extraction", SRC_DIR / "field_extractor.py"),
    ("Table Extraction", SRC_DIR / "table_extractor.py"),
    ("Invoice Integration", SRC_DIR / "invoice_integrator.py"),
    ("Invoice Validation", SRC_DIR / "invoice_validator.py"),
    ("Invoice Export", SRC_DIR / "export_invoice.py"),
]


# ============================================================
# NON-INVOICE DOCUMENT HANDLING
# ============================================================
# Non-invoice documents intentionally stop after OCR + classification.
# Their canonical page_*_ocr.txt files are displayed directly by app.py.
# No generic field/table interpretation is forced onto arbitrary documents.

# ============================================================
# CONSOLE
# ============================================================

def configure_console():
    """Use UTF-8 for Windows/Streamlit child-process output."""

    try:
        sys.stdout.reconfigure(
            encoding="utf-8",
            errors="replace",
        )
        sys.stderr.reconfigure(
            encoding="utf-8",
            errors="replace",
        )
    except Exception:
        pass


# ============================================================
# WORKING DIRECTORY
# ============================================================

def get_working_directory(input_file):
    """
    Return the isolated output directory for one input document.

    Example:
        input/Bata_Sandels.pdf
        -> output/Bata_Sandels/

    Every downstream module receives this directory explicitly.
    """

    return OUTPUT_DIR / input_file.stem


# ============================================================
# CLEAR ONE DOCUMENT WORKSPACE
# ============================================================

def clear_document_workspace(work_dir):
    """
    Remove the previous output for this document.

    This prevents stale JSON/CSV/OCR files from being mixed with
    the current run.
    """

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    if work_dir.exists():
        try:
            shutil.rmtree(work_dir)
        except Exception as error:
            print()
            print(f" Could not clear working directory: {work_dir}")
            print(f" Error: {error}")
            return False

    try:
        work_dir.mkdir(
            parents=True,
            exist_ok=True,
        )
    except Exception as error:
        print()
        print(f" Could not create working directory: {work_dir}")
        print(f" Error: {error}")
        return False

    return True


# ============================================================
# RUN SCRIPT
# ============================================================

def run_script(
    step_name,
    script_path,
    arguments=None,
):
    """
    Run one Python pipeline module.

    The same virtual environment/Python interpreter that runs
    main.py is used for every child process.
    """

    print()
    print("=" * 100)
    print(f"STEP: {step_name}")
    print("=" * 100)

    if not script_path.exists():
        print(f" Script not found: {script_path}")
        return False

    command = [
        sys.executable,
        str(script_path),
    ]

    if arguments:
        command.extend(
            str(argument)
            for argument in arguments
        )

    print(
        "Running: "
        + " ".join(command)
    )
    print()

    try:
        child_env = dict(os.environ)

        # Windows console / Streamlit compatibility.
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["PYTHONUTF8"] = "1"

        result = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            check=False,
            env=child_env,
        )

        if result.returncode != 0:
            print()
            print(f" {step_name} FAILED")
            print(f"Return code: {result.returncode}")
            return False

        print()
        print(f" {step_name} COMPLETED")
        return True

    except Exception as error:
        print()
        print(f" Error while running {step_name}")
        print(f"Error: {error}")
        return False


# ============================================================
# OCR
# ============================================================

def run_ocr(
    input_file,
    work_dir,
):
    """
    Run the OCR engine.

    pdf_ocr.py receives:
        1. input document
        2. explicit output directory
    """

    return run_script(
        "OCR",
        SRC_DIR / "pdf_ocr.py",
        [
            input_file,
            work_dir,
        ],
    )


# ============================================================
# CLASSIFICATION
# ============================================================

def classify_document(work_dir):
    """
    Classify the document using the OCR output from this
    document only.

    document_classifier.py writes document_type.json into
    work_dir.
    """

    success = run_script(
        "Document Classification",
        SRC_DIR / "document_classifier.py",
        [work_dir],
    )

    if not success:
        return None

    classification_file = (
        work_dir / "document_type.json"
    )

    if not classification_file.exists():
        print()
        print(" document_type.json was not created.")
        print(f"Expected: {classification_file}")
        return None

    try:
        import json

        with open(
            classification_file,
            "r",
            encoding="utf-8",
        ) as file:
            data = json.load(file)

        document_type = str(
            data.get(
                "document_type",
                "UNKNOWN",
            )
        ).strip().upper()

        if not document_type:
            document_type = "UNKNOWN"

        return document_type

    except Exception as error:
        print()
        print(" Could not read document classification.")
        print(f"Error: {error}")
        return None


# ============================================================
# AMAZON INVOICE DETECTION
# ============================================================

def is_amazon_invoice(work_dir, document_type):
    """
    Route only genuine Amazon invoices to the structured invoice
    pipeline.

    A document must already be classified as INVOICE and contain
    Amazon-specific evidence in its canonical OCR text.  Other
    invoices (for example Flipkart) remain on the generic OCR route.
    """
    if str(document_type).strip().upper() != "INVOICE":
        return False

    ocr_files = sorted(
        work_dir.glob("page_*_ocr.txt"),
        key=lambda path: (
            int(path.stem.split("_")[1])
            if len(path.stem.split("_")) > 1
            and path.stem.split("_")[1].isdigit()
            else 999999
        ),
    )

    if not ocr_files:
        return False

    try:
        combined_text = "\n".join(
            path.read_text(encoding="utf-8", errors="replace")
            for path in ocr_files
        ).lower()
    except Exception:
        return False

    # Amazon-specific evidence.  The document must be an invoice
    # already, so ordinary resumes containing Amazon technologies
    # cannot enter this route.
    amazon_markers = (
        "amazon.in",
        "amazon india",
        "amazon seller services",
        "amazon pay",
        "amazon marketplace",
        "amazon fulfillment",
        "amazon transport services",
    )

    return any(marker in combined_text for marker in amazon_markers)


# ============================================================
# INVOICE PIPELINE
# ============================================================

def process_invoice(work_dir):
    """
    Run the detailed structured invoice pipeline for an Amazon
    invoice. The extraction itself remains observation-based and
    is not hard-coded to individual field values.
    """

    print()
    print("#" * 100)
    print("UNIVERSAL INVOICE PIPELINE")
    print("#" * 100)

    for step_name, script_path in INVOICE_PIPELINE:

        success = run_script(
            step_name,
            script_path,
            [work_dir],
        )

        if not success:
            return False

    return True


# ============================================================
# GENERIC PIPELINE
# ============================================================

def process_generic_document(
    document_type,
    work_dir,
):
    """
    Complete the non-invoice route after OCR/classification.

    Arbitrary documents are intentionally not forced through an invoice-like
    schema.  The canonical page_*_ocr.txt files produced by pdf_ocr.py are
    preserved and presented directly by app.py.
    """

    print()
    print("#" * 100)
    print("NON-INVOICE DOCUMENT HANDLING")
    print("#" * 100)
    print()
    print(f"Document Type : {document_type}")
    print("Route         : OCR text display")

    ocr_files = sorted(
        work_dir.glob("page_*_ocr.txt"),
        key=lambda path: (
            int(path.stem.split("_")[1])
            if len(path.stem.split("_")) > 1
            and path.stem.split("_")[1].isdigit()
            else 999999
        ),
    )

    if not ocr_files:
        print(" No canonical OCR text files were produced.")
        return False

    print(f" OCR text pages : {len(ocr_files)}")
    for path in ocr_files:
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        print(f"   {path.name} ({size} bytes)")

    print()
    print(" No generic field/table interpretation applied.")
    print(" Original OCR observations remain available for display.")
    return True


# ============================================================
# ARCHIVE SAFETY
# ============================================================

def verify_work_dir(work_dir):
    """
    Verify that the pipeline actually produced files.

    An empty directory is treated as a failed run.
    """

    if not work_dir.exists():
        print()
        print(" Working directory does not exist.")
        print(f"Expected: {work_dir}")
        return False

    files = [
        item
        for item in work_dir.iterdir()
        if item.is_file()
    ]

    if not files:
        print()
        print(" Pipeline produced no output files.")
        print(f"Directory: {work_dir}")
        return False

    return True


# ============================================================
# PROCESS ONE DOCUMENT
# ============================================================

def process_document(input_file):
    """
    Complete document-processing flow.

        Input
          ↓
        OCR
          ↓
        Classification
          ↓
        Routing
          ↓
        Extraction
          ↓
        Integration
          ↓
        Validation / Export
          ↓
        Archived document workspace
    """

    print()
    print("#" * 100)
    print(f"PROCESSING DOCUMENT: {input_file.name}")
    print("#" * 100)

    work_dir = get_working_directory(
        input_file
    )

    # --------------------------------------------------------
    # Prepare isolated workspace
    # --------------------------------------------------------

    print()
    print("Preparing document working directory...")
    print(f"Work directory: {work_dir}")

    if not clear_document_workspace(work_dir):
        print()
        print(" Could not prepare document workspace.")
        return False

    print(" Working directory ready.")

    # --------------------------------------------------------
    # Step 1 - OCR
    # --------------------------------------------------------

    if not run_ocr(
        input_file,
        work_dir,
    ):
        print()
        print(" Document processing stopped at OCR.")
        return False

    # --------------------------------------------------------
    # Step 2 - Classification
    # --------------------------------------------------------

    document_type = classify_document(
        work_dir
    )

    if not document_type:
        print()
        print(" Document classification failed.")
        return False

    # --------------------------------------------------------
    # Step 3 - Routing
    # --------------------------------------------------------

    print()
    print("=" * 100)
    print("DOCUMENT ROUTING")
    print("=" * 100)

    print()
    print(f"Detected Type : {document_type}")

    if document_type == "INVOICE" and is_amazon_invoice(
        work_dir,
        document_type,
    ):
        print(
            "Route         : Amazon Invoice Pipeline"
        )

        success = process_invoice(
            work_dir
        )

    else:
        print(
            "Route         : Generic Document Pipeline"
        )

        success = process_generic_document(
            document_type,
            work_dir,
        )

    # --------------------------------------------------------
    # Failure
    # --------------------------------------------------------

    if not success:
        print()
        print("=" * 100)
        print("DOCUMENT PROCESSING FAILED")
        print("=" * 100)

        print()
        print(f"Document : {input_file.name}")
        print(f"Type     : {document_type}")
        print(f"Workspace: {work_dir}")

        return False

    # --------------------------------------------------------
    # Verify
    # --------------------------------------------------------

    if not verify_work_dir(work_dir):
        print()
        print(" Document processing failed verification.")
        return False

    # --------------------------------------------------------
    # Completed
    # --------------------------------------------------------

    print()
    print("=" * 100)
    print("DOCUMENT PROCESSING COMPLETED")
    print("=" * 100)

    print()
    print(f"Document     : {input_file.name}")
    print(f"Document Type: {document_type}")
    print(f"Output       : {work_dir}")

    print()

    return True


# ============================================================
# FIND INPUT FILES
# ============================================================

def find_input_files():
    INPUT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    return sorted(
        [
            path
            for path in INPUT_DIR.iterdir()
            if path.is_file()
            and path.suffix.lower()
            in SUPPORTED_EXTENSIONS
        ],
        key=lambda path: path.name.lower(),
    )


# ============================================================
# RESOLVE COMMAND-LINE INPUT
# ============================================================

def resolve_input_file(argument):
    """
    Resolve a user-provided path.

    Relative paths are resolved relative to the project root.
    """

    input_path = Path(argument)

    if not input_path.is_absolute():
        input_path = (
            PROJECT_ROOT / input_path
        )

    return input_path.resolve()


# ============================================================
# MAIN
# ============================================================

def main():
    configure_console()

    print()
    print("#" * 100)
    print("GENERIC OCR DOCUMENT PROCESSING PIPELINE")
    print("#" * 100)

    print()
    print(f"Project root : {PROJECT_ROOT}")
    print(f"Input folder : {INPUT_DIR}")
    print(f"Output folder: {OUTPUT_DIR}")

    # --------------------------------------------------------
    # Specific file
    # --------------------------------------------------------

    if len(sys.argv) > 1:

        input_path = resolve_input_file(
            sys.argv[1]
        )

        if not input_path.exists():
            print()
            print(" Input file not found.")
            print(f"Expected: {input_path}")
            return 1

        if (
            input_path.suffix.lower()
            not in SUPPORTED_EXTENSIONS
        ):
            print()
            print(" Unsupported input file type.")
            print(
                "Supported formats: "
                "PDF, PNG, JPG, JPEG, BMP, TIF, TIFF, WEBP"
            )
            return 1

        input_files = [
            input_path
        ]

    # --------------------------------------------------------
    # Automatic input discovery
    # --------------------------------------------------------

    else:
        input_files = find_input_files()

    # --------------------------------------------------------
    # No input
    # --------------------------------------------------------

    if not input_files:
        print()
        print(" No supported documents found.")
        print()
        print(
            "Place PDF/image files here:"
        )
        print(
            f"    {INPUT_DIR}"
        )
        print()
        print(
            "Or process a specific file:"
        )
        print(
            "    python main.py input\\document.pdf"
        )
        return 1

    # --------------------------------------------------------
    # Display inputs
    # --------------------------------------------------------

    print()
    print("=" * 100)
    print("INPUT DOCUMENTS")
    print("=" * 100)

    for index, input_file in enumerate(
        input_files,
        start=1,
    ):
        print(
            f"  {index}. {input_file.name}"
        )

    print()
    print(
        f"Total input files: {len(input_files)}"
    )

    # --------------------------------------------------------
    # Process
    # --------------------------------------------------------

    successful = []
    failed = []

    for input_file in input_files:

        success = process_document(
            input_file
        )

        if success:
            successful.append(
                input_file.name
            )
        else:
            failed.append(
                input_file.name
            )

    # --------------------------------------------------------
    # Final summary
    # --------------------------------------------------------

    print()
    print("#" * 100)
    print("FINAL PIPELINE SUMMARY")
    print("#" * 100)

    print()
    print(
        f"Total documents: {len(input_files)}"
    )
    print(
        f"Successful     : {len(successful)}"
    )
    print(
        f"Failed         : {len(failed)}"
    )

    if successful:
        print()
        print("SUCCESSFUL DOCUMENTS")
        for filename in successful:
            print(
                f"   {filename}"
            )

    if failed:
        print()
        print("FAILED DOCUMENTS")
        for filename in failed:
            print(
                f"   {filename}"
            )

    print()
    print("OUTPUT FOLDERS")

    for input_file in input_files:

        output_folder = (
            OUTPUT_DIR /
            input_file.stem
        )

        if output_folder.exists():
            print(
                f"   {output_folder}"
            )

    print()
    print("#" * 100)
    print("END")
    print("#" * 100)
    print()

    return 0 if not failed else 1


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    raise SystemExit(main())
