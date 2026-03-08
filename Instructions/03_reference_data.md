# Reference Data

## Principles

All dimensional lookups are loaded from external CSV files at startup. The application never has reference data hardcoded in Python source. This makes lookups replaceable without code changes and keeps the data model honest about what is configuration versus what is logic.

Implement all reference loading in `reference.py`.

---

## Loading pattern

At startup, attempt to load each reference file from the application's working directory. If a file is not present:
- Log a warning at startup (not an error -- the app should still start)
- Fall back to returning the raw code value in any lookup call
- The missing-file state must be visible in the `/api/health` response

```python
class ReferenceData:
    def __init__(self):
        self.toc_names: dict[str, str] = {}
        self.stanox_names: dict[str, str] = {}
        self.toc_ref_loaded: bool = False
        self.stanox_ref_loaded: bool = False

    def load_all(self) -> None:
        self._load_toc_ref()
        self._load_stanox_ref()

    def resolve_toc(self, toc_id: str) -> str:
        return self.toc_names.get(toc_id, toc_id)  # fallback: raw code

    def resolve_stanox(self, stanox: str) -> str:
        return self.stanox_names.get(stanox, stanox)  # fallback: raw code
```

---

## TOC reference

**Source**: Open Rail Data community wiki -- https://wiki.openraildata.com/index.php/TOC_Codes

**Setup step (one-time, automated)**: Write a script `scripts/fetch_toc_ref.py` that:
1. Fetches the HTML from the above URL
2. Parses the table on the page -- the relevant columns are the operator long name and the "Sector Code" (this is the two-digit code that appears as `toc_id` in TRUST messages)
3. Writes the output to `toc_ref.csv` with columns `toc_id`, `toc_name`

This script should be run once during setup and its output committed (or at minimum documented) alongside the app. It is not run at app startup.

**Important caveats to document in the README**:
- The wiki is a community-maintained source, not an official NR publication. The output file should be treated as editable and may need manual correction.
- Some operators may be missing or have incorrect sector codes. The app degrades gracefully (displays raw `toc_id`) for any unmatched code.

**Loading**:

`toc_id` must be treated as a string throughout. The feed delivers it as a string, and zero-padded codes (e.g. `"08"`) must not be cast to integers. Load into `self.toc_names` as `{toc_id_str: toc_name_str}`.

---

## STANOX reference

**Source file**: `CORPUSExtract.json` -- obtainable from the Network Rail open data portal. The user will supply this file.

**File format**: JSON. The relevant structure is an array of location objects. Each object contains (among other fields) a `STANOX` field and a `NLCDESC` or `TIPLOC` field for the location name. Before implementing the parser, inspect the actual structure of the file and confirm which field to use as the display name -- ask the user if the right field is not obvious from inspection.

**Loading**: Parse the JSON at startup, extract `STANOX` → location name pairs, load into `self.stanox_names` as `{stanox_str: location_name_str}`.

STANOX codes in the feed are five-digit strings (e.g. `"88481"`). Ensure the lookup key format matches the feed format exactly -- normalise both sides to strings without leading-zero stripping.

**If the file is absent**: log a warning, continue with empty lookup, display raw STANOX codes in the UI.

---

## README documentation

The README must include a section titled "Reference data" that covers:

1. How to obtain `CORPUSExtract.json` (Network Rail open data portal) and where to place it
2. How to generate `toc_ref.csv` by running `scripts/fetch_toc_ref.py`, with a note that the output is from a community wiki and may need manual correction
3. What happens if either file is absent (app starts, raw codes displayed)
4. The `/api/health` response flags which reference files are loaded -- check this after startup
