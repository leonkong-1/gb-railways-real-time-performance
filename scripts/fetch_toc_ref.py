"""
Fetch TOC reference data from the Open Rail Data wiki and write toc_ref.csv.

Usage:
    python scripts/fetch_toc_ref.py [--output PATH]

Output columns: toc_id, toc_name

For each operator the wiki table has three code columns:
  - Business Code (2-letter, e.g. "ED")
  - Sector Code   (numeric, e.g. "23")   ← used in TRUST feed toc_id field
  - ATOC Code     (usually same as Business Code)

We emit one CSV row per code so that both the Business Code lookup and the
numeric Sector Code lookup resolve to the operator name.  Sector codes of "?"
(unknown) are skipped.

The source is a community-maintained wiki, not an official NR publication.
The output file should be treated as editable and may need manual correction.

Run once during setup; re-run if the operator list changes.
The app degrades gracefully (displays raw toc_id) for any unmatched code.
"""

import argparse
import csv
import sys
from pathlib import Path

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("Missing dependencies. Run: pip install requests beautifulsoup4")
    sys.exit(1)

TOC_WIKI_URL = "https://wiki.openraildata.com/index.php/TOC_Codes"
DEFAULT_OUTPUT = Path(__file__).parent.parent / "toc_ref.csv"


def fetch_toc_codes(url: str) -> list[dict]:
    """Scrape TOC codes table from the Open Rail Data wiki.

    The main table (Table 2 on the page) has columns:
      Company Name | Business Code | Sector Code | ATOC Code

    We emit a row for each non-empty code so both the Business Code and the
    numeric Sector Code resolve to the operator name.
    """
    print(f"Fetching {url} ...")
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    results = []

    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        headers = [th.get_text(strip=True).lower() for th in rows[0].find_all(["th", "td"])]

        # Find the main TOC table: must have both "sector" and "business" columns.
        sector_col = None
        business_col = None
        name_col = None

        for i, h in enumerate(headers):
            if "sector" in h:
                sector_col = i
            if "business" in h:
                business_col = i
            if "company" in h or "name" in h or "operator" in h:
                name_col = i

        if sector_col is not None and business_col is not None and name_col is not None:
            # This is the main operator table — emit rows for both code types.
            for row in rows[1:]:
                cells = row.find_all(["td", "th"])
                if len(cells) <= max(sector_col, business_col, name_col):
                    continue
                name        = cells[name_col].get_text(strip=True)
                biz_code    = cells[business_col].get_text(strip=True)
                sector_code = cells[sector_col].get_text(strip=True)

                if not name:
                    continue
                if biz_code and len(biz_code) <= 4:
                    results.append({"toc_id": biz_code, "toc_name": name})
                if sector_code and sector_code != "?" and len(sector_code) <= 4:
                    results.append({"toc_id": sector_code, "toc_name": name})
        elif name_col is None and business_col is None and sector_col is None:
            # Fallback: single-column code tables (e.g. the old BR sector codes table).
            code_col = None
            fallback_name_col = None
            for i, h in enumerate(headers):
                if "sector" in h or "code" in h or "toc" in h:
                    code_col = i
                if "operator" in h or "name" in h or "train" in h or "company" in h or "description" in h:
                    fallback_name_col = i
            if code_col is None and len(headers) >= 2:
                code_col, fallback_name_col = 0, 1
            if code_col is not None and fallback_name_col is not None:
                for row in rows[1:]:
                    cells = row.find_all(["td", "th"])
                    if len(cells) <= max(code_col, fallback_name_col):
                        continue
                    toc_id   = cells[code_col].get_text(strip=True)
                    toc_name = cells[fallback_name_col].get_text(strip=True)
                    if toc_id and len(toc_id) <= 4 and toc_name:
                        results.append({"toc_id": toc_id, "toc_name": toc_name})

    return results


def write_csv(records: list[dict], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["toc_id", "toc_name"])
        writer.writeheader()
        writer.writerows(records)
    print(f"Wrote {len(records)} TOC codes to {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help=f"Output CSV path (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()

    records = fetch_toc_codes(TOC_WIKI_URL)
    if not records:
        print("WARNING: No TOC codes parsed. Check the wiki page structure and update the scraper.")
        sys.exit(1)

    write_csv(records, args.output)
    print("Done. Review the output file and correct any mismatched codes manually.")
    print("NOTE: This data is from a community wiki and may need manual corrections.")


if __name__ == "__main__":
    main()
