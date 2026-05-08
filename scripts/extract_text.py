import argparse
import sys
from pathlib import Path

from packages.shared.documents.extract_text import SUPPORTED_EXTENSIONS, extract_text, iter_files


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    parser = argparse.ArgumentParser(description="Extract text from Office and PDF files.")
    parser.add_argument("paths", nargs="+", help="File or folder paths")
    parser.add_argument("--recursive", action="store_true", help="Walk folders recursively")
    parser.add_argument("--chars", type=int, default=6000, help="Maximum characters per file")
    args = parser.parse_args()

    files = iter_files([Path(path) for path in args.paths], args.recursive)
    if not files:
        print("No supported files found.", file=sys.stderr)
        return 1

    for file_path in files:
        print(f"=== {file_path} ===")
        try:
            text = extract_text(file_path)
        except Exception as exc:
            print(f"[extract-error] {exc}")
        else:
            text = (text or "").strip()
            if not text:
                print("[no extracted text]")
            else:
                print(text[: args.chars])
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
