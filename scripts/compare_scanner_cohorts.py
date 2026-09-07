#!/usr/bin/env python3
"""Compare paired local JSON exports; stdout only, no DB/network/write access.

Usage: python scripts/compare_scanner_cohorts.py path/to/paired-export.json
Schema: modules.scanner_cohort_comparison.compare_exported_cohorts docstring.
"""

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from modules.scanner_cohort_comparison import compare_exported_cohorts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export", type=Path)
    args = parser.parse_args()
    try:
        result = compare_exported_cohorts(json.loads(args.export.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.exit(2, f"Vergleich nicht verfuegbar: {exc}\n")
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
