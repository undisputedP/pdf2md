import os
import re
import glob
import time
from datetime import datetime

import pymupdf
import pymupdf4llm


# Math symbols that indicate equation-heavy content
MATH_SYMBOLS = set("∫∑∂√±≤≥≠≈∞∝∈∉∀∃∇∆∏∐⊂⊃⊆⊇∩∪⊕⊗⊥∧∨¬⇒⇔→←↔∘×÷")
MATH_PATTERNS = re.compile(
    r"(\bfrac\b|\bint\b|\bsum\b|\blim\b|\binfty\b"
    r"|\^\{|\}_|\\\(|\\\)|\\\[|\\\]"  # LaTeX delimiters
    r"|[=<>]{2,}|[α-ωΑ-Ω]"           # Greek letters
    r"|d[xy]/d[xy]|\\partial)",        # derivatives
    re.IGNORECASE,
)


def is_english(text: str) -> bool:
    """Check if a line is predominantly English (ASCII letters)."""
    if not text.strip():
        return True
    letters = re.findall(r"[a-zA-Z]", text)
    all_letters = re.findall(r"\S", text)
    if not all_letters:
        return True
    return len(letters) / len(all_letters) > 0.5


def filter_english(markdown: str) -> str:
    """Keep only English lines from markdown text."""
    lines = markdown.split("\n")
    filtered = [line for line in lines if is_english(line)]
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(filtered))
    return result.strip() + "\n"


def is_math_heavy(pdf_path: str, threshold: float = 0.02) -> bool:
    """Quick-scan PDF to detect if it's math-heavy.

    Returns True if math symbol/pattern density exceeds threshold.
    """
    doc = pymupdf.open(pdf_path)
    total_chars = 0
    math_hits = 0

    for page in doc:
        text = page.get_text()
        total_chars += len(text)
        math_hits += sum(1 for ch in text if ch in MATH_SYMBOLS)
        math_hits += len(MATH_PATTERNS.findall(text))

    doc.close()

    if total_chars == 0:
        return True  # empty/image PDF -> use Marker for OCR

    density = math_hits / total_chars
    return density > threshold


def convert_with_pymupdf(pdf_path: str) -> str:
    """Fast conversion using PyMuPDF4LLM."""
    return pymupdf4llm.to_markdown(pdf_path)


def convert_with_marker(pdf_path: str, converter) -> str:
    """High-quality conversion using Marker (for math content)."""
    rendered = converter(pdf_path)
    return rendered.markdown


def log(log_path: str, message: str):
    """Append a timestamped line to log.txt."""
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {message}\n"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line)
    print(message)


def check_kill_switch(base_dir: str) -> bool:
    """Check if STOP file exists (kill switch)."""
    return os.path.exists(os.path.join(base_dir, "STOP"))


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    input_dir = os.path.join(base_dir, "input")
    output_dir = os.path.join(base_dir, "output")
    log_path = os.path.join(base_dir, "log.txt")
    os.makedirs(output_dir, exist_ok=True)

    all_pdfs = sorted(glob.glob(os.path.join(input_dir, "*.pdf")))
    pdf_files = [
        f for f in all_pdfs
        if not os.path.basename(f).startswith("_done") and not f.endswith("_done.pdf")
    ]

    if not pdf_files:
        log(log_path, "No unprocessed PDF files found in input/ folder.")
        return

    log(log_path, f"=== Session started: {len(pdf_files)} PDF(s) to process ===")

    # Lazy-load Marker only if needed
    marker_converter = None

    for i, pdf_path in enumerate(pdf_files, 1):
        if check_kill_switch(base_dir):
            log(log_path, "STOP file detected. Halting processing.")
            break

        name = os.path.splitext(os.path.basename(pdf_path))[0]
        start = time.time()

        # Decide converter
        math_heavy = is_math_heavy(pdf_path)
        engine = "Marker" if math_heavy else "PyMuPDF4LLM"
        log(log_path, f"[{i}/{len(pdf_files)}] {name}.pdf -> {engine} ... ")

        try:
            if math_heavy:
                if marker_converter is None:
                    log(log_path, "Loading Marker models (first math PDF)...")
                    from marker.converters.pdf import PdfConverter
                    from marker.config.parser import ConfigParser
                    from marker.models import create_model_dict

                    config_parser = ConfigParser({"output_format": "markdown", "languages": ["en"]})
                    artifact_dict = create_model_dict()
                    marker_converter = PdfConverter(
                        artifact_dict=artifact_dict,
                        config=config_parser.generate_config_dict(),
                    )
                markdown = convert_with_marker(pdf_path, marker_converter)
            else:
                markdown = convert_with_pymupdf(pdf_path)

            markdown = filter_english(markdown)

            out_path = os.path.join(output_dir, f"{name}.md")
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(markdown)

            elapsed = time.time() - start
            basename = os.path.basename(pdf_path)
            done_path = os.path.join(input_dir, f"_done{basename}")
            os.rename(pdf_path, done_path)
            log(log_path, f"  -> OK ({elapsed:.1f}s)")

        except Exception as e:
            elapsed = time.time() - start
            log(log_path, f"  -> FAILED ({elapsed:.1f}s): {e}")

    log(log_path, "=== Session ended ===\n")


if __name__ == "__main__":
    main()
