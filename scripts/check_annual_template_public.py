"""Stdlib OOXML gate: the sole publishable XLSX must be an empty template."""
from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

try:
    from .annual_template_contract import CHECK_FORMULAS, ROOT, SHEETS, confirmed_rows, headers
except ImportError:
    from annual_template_contract import CHECK_FORMULAS, ROOT, SHEETS, confirmed_rows, headers

OFFICIAL = "templates/FORECAST_Towell_Plantilla_Carga_Anual.xlsx"
NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
ALLOWED = re.compile(r"^(?:\[Content_Types\]\.xml|_rels/\.rels|docProps/(?:app|core)\.xml|xl/(?:workbook\.xml|styles\.xml|theme/theme1\.xml|_rels/workbook\.xml\.rels|worksheets/sheet[1-8]\.xml|worksheets/_rels/sheet[1-8]\.xml\.rels|tables/table[1-8]\.xml))$")


def audit_template(path: Path) -> list[str]:
    problems = []
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or any(not ALLOWED.fullmatch(n) for n in names):
                return ["unexpected_package_part"]
            if sum(i.file_size for i in archive.infolist()) > 2_000_000:
                return ["template_too_large"]
            for name in names:
                raw = archive.read(name)
                root = ET.fromstring(raw)
                if any(prefix.encode() in raw for prefix in ("sk-" + "proj-", "gh" + "p_", "gh" + "o_", "gl" + "pat-")):
                    problems.append("possible_secret_in_package")
                if name.endswith(".rels") and any(r.get("TargetMode") == "External" for r in root):
                    problems.append("external_relationship")
            workbook = ET.fromstring(archive.read("xl/workbook.xml"))
            if [s.get("name") for s in workbook.findall("s:sheets/s:sheet", NS)] != list(SHEETS):
                problems.append("sheet_contract_changed")
            for i, name in enumerate(SHEETS, 1):
                root = ET.fromstring(archive.read(f"xl/worksheets/sheet{i}.xml"))
                rows = {}
                for row in root.findall("s:sheetData/s:row", NS):
                    values = []
                    for cell in row.findall("s:c", NS):
                        formula = cell.find("s:f", NS)
                        texts = cell.findall("s:is//s:t", NS)
                        scalar = cell.find("s:v", NS)
                        value = "".join(t.text or "" for t in texts) if texts else scalar.text if scalar is not None else ""
                        if formula is not None:
                            if name != "07_VALIDACIONES":
                                problems.append("formula_outside_checks")
                            value = "=" + (formula.text or "")
                            if value not in CHECK_FORMULAS:
                                problems.append("unrecognized_formula")
                        if value:
                            values.append((cell.get("r"), value))
                    if values:
                        rows[int(row.get("r"))] = values
                header = rows.pop(1, [])
                if [v for _, v in header] != headers(name):
                    problems.append(f"header_changed:{name}")
                if name in {"01_CONTROL_CARGA", "02_PRODUCTOS", "03_HISTORICO_MENSUAL", "04_FORECAST_CLIENTE", "05_FUENTES"} and rows:
                    problems.append(f"business_data_present:{name}")
                if name == "06_DICCIONARIOS":
                    actual = [[v for _, v in cells] for _, cells in sorted(rows.items())]
                    if actual != confirmed_rows():
                        problems.append("dictionary_not_exact_confirmed_seed")
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, ET.ParseError):
        problems.append("invalid_template_package")
    return sorted(set(problems))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", nargs="?", type=Path, default=ROOT / OFFICIAL)
    args = parser.parse_args()
    problems = audit_template(args.file)
    if problems:
        raise SystemExit("\n".join(problems))
    print("annual_template_public_ok: 8 sheets; 0 business records; confirmed dictionary only")


if __name__ == "__main__":
    main()
