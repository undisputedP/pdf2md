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

# Engine labels
ENGINE_PYMUPDF = "PyMuPDF4LLM"
ENGINE_MARKER = "Marker"


def is_english(text: str) -> bool:
    if not text.strip():
        return True
    letters = re.findall(r"[a-zA-Z]", text)
    all_letters = re.findall(r"\S", text)
    if not all_letters:
        return True
    return len(letters) / len(all_letters) > 0.5


def filter_english(markdown: str) -> str:
    lines = markdown.split("\n")
    filtered = [line for line in lines if is_english(line)]
    result = re.sub(r"\n{3,}", "\n\n", "\n".join(filtered))
    return result.strip() + "\n"


def is_math_heavy(pdf_path: str, threshold: float = 0.02) -> bool:
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
        return True
    return math_hits / total_chars > threshold


def get_page_count(pdf_path: str) -> int:
    doc = pymupdf.open(pdf_path)
    count = len(doc)
    doc.close()
    return count


def convert_with_pymupdf(pdf_path: str) -> str:
    return pymupdf4llm.to_markdown(pdf_path)


def convert_with_marker(pdf_path: str, converter) -> str:
    rendered = converter(pdf_path)
    return rendered.markdown


def log(log_path: str, message: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {message}\n"
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(line)
    print(message)


def check_kill_switch(base_dir: str) -> bool:
    return os.path.exists(os.path.join(base_dir, "STOP"))


def load_marker():
    from marker.converters.pdf import PdfConverter
    from marker.config.parser import ConfigParser
    from marker.models import create_model_dict

    config_parser = ConfigParser({"output_format": "markdown", "languages": ["en"]})
    artifact_dict = create_model_dict()
    return PdfConverter(
        artifact_dict=artifact_dict,
        config=config_parser.generate_config_dict(),
    )


def format_time(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    elif seconds < 3600:
        m, s = divmod(seconds, 60)
        return f"{int(m)}m {int(s)}s"
    else:
        h, rem = divmod(seconds, 3600)
        m, s = divmod(rem, 60)
        return f"{int(h)}h {int(m)}m"


def estimate_batch_time(pdf_files: list, mode: str, benchmark_time: float, benchmark_pages: int) -> str:
    """Estimate total time based on first-file benchmark."""
    if benchmark_pages == 0:
        return "unknown"
    per_page = benchmark_time / benchmark_pages
    total_pages = sum(get_page_count(f) for f in pdf_files)
    est = per_page * total_pages
    return format_time(est)


def show_menu(pdf_files: list) -> str:
    total_pages = sum(get_page_count(f) for f in pdf_files)

    print("\n" + "=" * 60)
    print(f"  PDF to Markdown Converter")
    print(f"  {len(pdf_files)} file(s), ~{total_pages} pages total")
    print("=" * 60)
    print()
    print("  Choose conversion engine:")
    print()
    print("  [1] PyMuPDF4LLM  - Fast, text-based PDFs     (~1-3s/file)")
    print("  [2] Marker        - Math/equations, deep ML    (~2-5min/file)")
    print("  [3] Auto          - Auto-detect per file")
    print("                      (math -> Marker, else -> PyMuPDF4LLM)")
    print()

    while True:
        choice = input("  Your choice [1/2/3]: ").strip()
        if choice in ("1", "2", "3"):
            return {"1": ENGINE_PYMUPDF, "2": ENGINE_MARKER, "3": "auto"}[choice]
        print("  Invalid choice. Enter 1, 2, or 3.")


def get_engine_for_file(pdf_path: str, mode: str) -> str:
    if mode == "auto":
        return ENGINE_MARKER if is_math_heavy(pdf_path) else ENGINE_PYMUPDF
    return mode


def main():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    input_dir = os.path.join(base_dir, "input")
    output_dir = os.path.join(base_dir, "output")
    log_path = os.path.join(base_dir, "log.txt")
    os.makedirs(output_dir, exist_ok=True)

    all_pdfs = sorted(glob.glob(os.path.join(input_dir, "*.pdf")))
    pdf_files = [
        f for f in all_pdfs
        if not os.path.basename(f).startswith("_done")
    ]

    if not pdf_files:
        print("No unprocessed PDF files found in input/ folder.")
        return

    mode = show_menu(pdf_files)
    log(log_path, f"=== Session started: {len(pdf_files)} PDF(s), engine={mode} ===")

    marker_converter = None

    first_file_done = False

    for i, pdf_path in enumerate(pdf_files, 1):
        if check_kill_switch(base_dir):
            log(log_path, "STOP file detected. Halting processing.")
            break

        name = os.path.splitext(os.path.basename(pdf_path))[0]
        pages = get_page_count(pdf_path)
        engine = get_engine_for_file(pdf_path, mode)
        start = time.time()

        log(log_path, f"[{i}/{len(pdf_files)}] {name}.pdf ({pages}p) -> {engine}")

        try:
            if engine == ENGINE_MARKER:
                if marker_converter is None:
                    log(log_path, "  Loading Marker models...")
                    marker_converter = load_marker()
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

            # After first file, show time estimate for remaining batch
            if not first_file_done and len(pdf_files) > 1:
                first_file_done = True
                remaining = pdf_files[i:]
                if remaining:
                    est = estimate_batch_time(remaining, mode, elapsed, pages)
                    log(log_path, f"  ** Estimated time for remaining {len(remaining)} file(s): ~{est}")

        except Exception as e:
            elapsed = time.time() - start
            log(log_path, f"  -> FAILED ({elapsed:.1f}s): {e}")

    log(log_path, "=== Session ended ===\n")


if __name__ == "__main__":
    main()
