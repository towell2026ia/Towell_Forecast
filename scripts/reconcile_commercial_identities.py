"""Create private, read-only PRD 3A.5 reconciliation artifacts from 3A.3/3A.4 exports."""

import argparse
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.inventory_preflight.automatic_reconciliation import load_json, reconcile, sha256, summary


def write_json(path: Path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("reconciliation", "review", "current", "missing", "out"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--accreditation", type=Path, help="Optional structured business evidence, never inferred")
    args = parser.parse_args()
    source = load_json(args.reconciliation)
    prior = load_json(args.review)["rows"]
    current = load_json(args.current)
    missing = {r["identity"]: r for r in load_json(args.missing)}
    accreditation = load_json(args.accreditation) if args.accreditation else None
    if len(source) != 427 or len(missing) != 49:
        raise ValueError("PRD3A3_SOURCE_COVERAGE_CHANGED")
    results = reconcile(source, prior, current, accreditation)
    for row in results:
        hint = missing.get(row["identity"])
        row["investigation_suggestions"] = hint["same_description_product_ids"] if hint else []
        row["inventory_link_state"] = "PENDIENTE_DE_VINCULACION" if row["status"] != "COINCIDENCIA_VERIFICADA" else "SIN_DATO"
    overview = summary(source, results)
    if overview["commercial_groups"] != 151:
        raise ValueError("COMMERCIAL_GROUPS_CHANGED")
    files = {"reconciliation": args.reconciliation, "review": args.review, "current": args.current, "missing": args.missing}
    if args.accreditation:
        files["accreditation"] = args.accreditation
    overview["input_sha256"] = {name: sha256(path) for name, path in files.items()}
    overview["production_load"] = False
    exceptions = [r for r in results if r["status"] != "COINCIDENCIA_VERIFICADA"]
    verified = [r for r in results if r["status"] == "COINCIDENCIA_VERIFICADA"]
    args.out.mkdir(parents=True, exist_ok=False)
    write_json(args.out / "correspondencias_verificadas.json", {"summary": overview, "rows": verified})
    write_json(args.out / "matriz_correspondencias.json", {"summary": overview, "rows": results})
    write_json(args.out / "excepciones.json", {"summary": overview, "rows": exceptions})
    write_json(args.out / "integridad_cobertura.json", overview)
    with (args.out / "matriz_cadena_modelo_color.csv").open("x", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(("identidad", "cuenta", "cadena", "formato", "UPC", "modelo", "presentación", "color",
                         "código_comercial", "grupo_comercial", "estado", "producto_verificado", "cadena_verificada",
                         "candidatos_UPC", "atributos_diferentes", "motivo", "evidencia"))
        for r in results:
            writer.writerow((r["identity"], r["account"], r["chain"], r["format"], r["upc"], r["model"],
                             r["presentation"], r["color"], r["commercial_code"], "|".join(r["group_key"]),
                             r["status"], r["verified_product_id"] or "", r["verified_chain_id"] or "",
                             "|".join(r["candidate_ids"]), "|".join(r["differences"]), r["reason"],
                             "|".join(r["evidence"])))
    with (args.out / "excepciones.csv").open("x", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(("identidad", "cuenta", "cadena", "formato", "UPC", "modelo", "presentación", "color",
                         "código_comercial", "candidatos", "tipo_excepción", "campos_diferentes", "motivo",
                         "estado", "evidencia", "fecha_revisión", "sugerencias_no_verificadas"))
        for r in exceptions:
            writer.writerow((r["identity"], r["account"], r["chain"], r["format"], r["upc"], r["model"],
                             r["presentation"], r["color"], r["commercial_code"], "|".join(r["candidate_ids"]),
                             r["status"], "|".join(r["differences"]), r["reason"],
                             "SIN_CORRESPONDENCIA" if r["status"] == "SIN_CORRESPONDENCIA" else "PENDIENTE",
                             "", "", "|".join(r["investigation_suggestions"])))
    print(json.dumps({k: overview[k] for k in ("identities", "commercial_groups", "verified", "review_required", "without_correspondence", "ambiguous")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
