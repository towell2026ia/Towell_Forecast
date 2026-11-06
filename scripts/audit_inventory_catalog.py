"""Read private local snapshots and write a NEW PRD 3A.3 reconciliation report.

No Supabase client, no mutations of source artifacts, no forecasting.
"""
import argparse
from collections import Counter
import csv
import hashlib
import html
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.inventory_preflight.reconciliation import reconcile  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--lineage", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    load = lambda p: json.loads(p.read_text(encoding="utf-8-sig"))
    bundle, catalog, current = load(args.bundle), load(args.catalog), load(args.current)
    products = bundle["versions"][-1]["products"]
    rows = reconcile(products, catalog, current["products"])
    if len(rows) != 427:
        raise ValueError("Expected complete 427-identity audit; never silently omit records")
    counts = dict(Counter(r["candidate_status"] for r in rows))
    groups = {(p["account"], p["chain"], p["format"], p["model"].upper(), p["size"].upper(), p["commercial_code"] or p["id"]) for p in products}
    summary = {"identities": len(rows), "database_products": len(current["products"]), "commercial_groups": len(groups),
               "candidates_by_exact_upc": counts, "one_format_hint": sum(r["format_hint_count"] == 1 for r in rows),
               "multiple_format_hints": sum(r["format_hint_count"] > 1 for r in rows),
               "no_format_hint": sum(r["format_hint_count"] == 0 for r in rows),
               "approved_mappings": 0, "blocked_identities": len(rows), "inventory_observations_loaded": 0,
               "decision": "STOP_OPERATIONAL_INTEGRATION", "reason": "No approved account/scope/product crosswalk, store universe or inventory source. Candidate matches are not approvals.",
               "source_confirmation": {"Towell": "historical internal forecast", "Cliente": "received forecast", "Pedidos": "actual order", "Venta": "independent platform SALES; not joined to private pilot", "Entrega": "independent platform DELIVERY; not joined to private pilot"},
               "input_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.bundle, args.catalog, args.current, args.sources, args.lineage)}}
    args.output.mkdir(parents=True, exist_ok=True)
    def write(name, value):
        with (args.output/name).open("x", encoding="utf-8") as out:
            json.dump(value, out, ensure_ascii=False, indent=2)
    write("conciliacion_427.json", rows)
    write("resumen_auditoria.json", summary)
    with (args.output/"conciliacion_427.csv").open("x", encoding="utf-8-sig", newline="") as out:
        keys = ["id", "account", "chain", "format", "barcode", "description", "model", "size", "color", "commercial_code", "candidate_status", "candidate_count", "format_hint_count", "integration_status", "reason"]
        w = csv.DictWriter(out, fieldnames=keys); w.writeheader()
        for r in rows:
            w.writerow({k: ("'"+str(r[k]) if str(r[k]).startswith(("=", "+", "-", "@")) else r[k]) for k in keys})
    scopes = Counter((r["account"], r["format"], r["candidate_status"]) for r in rows)
    write("scope_counts.json", [{"account": a, "format": f, "candidate_status": s, "count": n} for (a, f, s), n in sorted(scopes.items())])
    totals = Counter()
    for s in load(args.sources): totals[s["metric_code"]] += s["observations"]
    sources = [
        ["Excel privado / Towell", "K:M, anualidad según cabecera y mes de fila", "history.Towell (JSON privado; no DB)", "Fcst Towell histórico", "Confirmación de negocio PRD 3A.3; cabecera literal Venta no define la variable"],
        ["Excel privado / Cliente", "K:M", "history.Cliente (JSON privado; no DB)", "Fcst Cliente recibido", "Confirmación PRD 3A.3; fecha original de emisión no acreditada"],
        ["Excel privado / Pedidos", "K:M", "history.Pedidos (JSON privado; no DB)", "Pedido real", "Confirmación PRD 3A.3; no equivale a Venta ni Entrega"],
        ["Bootstrap histórico certificado / BAASE", "V: Venta; W: Pedido; X: Entrega", "monthly_observations.value + metric_code SALES / ORDER / DELIVERY", "Venta / Pedido / Entrega", "Trazabilidad literal por source_evidence; disponibilidad temporal UNKNOWN"],
        ["Fuentes Walmart", "POS Qty / Total Eaches Str Ordered / Total Eaches Str Received; columnas por año/mes", "monthly_observations.value + metric_code", "Venta / Pedido / Entrega", "Ver nueve celdas acreditadas abajo. No sumatoria automática entre scopes padre/hijo"],
        ["Inventario PV", "Tienda + SKU/color + fecha + versión + piezas", "pos_inventory_snapshots / pos_inventory_observations (SQL previo no aplicado)", "Inventario PIEZAS", "SIN FUENTE ACREDITADA; integración bloqueada"],
    ]
    write("correspondencia_fuentes.json", {"rows": sources, "platform_observations": totals, "temporal_evidence": current["evidence"]})
    def table(headers, data):
        e = lambda value: html.escape(str(value) if value is not None else "")
        return "<table><thead><tr>"+"".join("<th>"+e(h)+"</th>" for h in headers)+"</tr></thead><tbody>"+"".join("<tr>"+"".join("<td>"+e(c)+"</td>" for c in row)+"</tr>" for row in data)+"</tbody></table>"
    lineage = []
    for s in load(args.lineage):
        f = json.loads(s["description"])["canonical_fact"]
        lineage.append([s["metric_code"], f["sheet"]+"!"+f["cell"], f["key"][2], f["canonical_code"], f["value"], s["source_hash"], s["evidence_level"]])
    doc = '<!doctype html><html lang="es"><meta charset="utf-8"><title>PRD 3A.3 — Auditoría privada</title><style>body{font:15px system-ui;margin:32px;color:#20322d}table{border-collapse:collapse;width:100%;font-size:12px;margin:20px 0}td,th{border:1px solid #ccc;padding:7px;overflow-wrap:anywhere}th{background:#e6f1ec}pre{white-space:pre-wrap}.stop{background:#fff0d0;padding:16px}</style><h1>FORECAST Towell · PRD 3A.3</h1>'
    doc += '<p class="stop"><b>STOP integración operativa.</b> Preparación local y auditoría completadas. Ninguna coincidencia candidata habilita una carga. No se aplicaron SQL, inventarios reales ni despliegues.</p>'
    doc += '<h2>Conciliación completa</h2><pre>'+html.escape(json.dumps(summary, ensure_ascii=False, indent=2))+'</pre>'
    doc += '<h2>Fuentes y etiquetas</h2>'+table(["Fuente", "Columna", "Campo", "Etiqueta", "Evidencia / límite"], sources)
    doc += '<p>La interpretación de Towell como venta en el piloto anterior queda invalidada. Sus proyecciones se conservan como extrapolaciones del forecast interno histórico, no como un nuevo pronóstico entrenado sobre venta real. WAPE y motores no se modificaron. Los indicadores heredados de Towell/Cliente no acreditan precisión contra venta real. No hay unión de fuentes productivas al piloto privado ni nueva certificación LIVE.</p>'
    doc += '<h2>Linaje leído de Supabase</h2>'+table(["Métrica", "Hoja / celda", "Periodo", "Scope", "Valor", "SHA256 fuente", "Evidencia temporal"], lineage)
    doc += '<h2>Decisiones necesarias</h2><ol><li>Aprobar correspondencia exacta cuenta + formato + UPC → chain_id + product_id; las descripciones y señales de texto sólo ayudan a revisar.</li><li>Resolver productos ausentes y UPC con varios scopes; no confundir Walmart padre con BD/SC ni Batas Dama/Caballero con una tienda.</li><li>Acreditar universo de tiendas, fuente de inventario y responsable, fecha y versión. PV en el Excel privado no se interpreta como tienda.</li><li>Acreditar Venta y Entrega para cada identidad privada y su vigencia antes de conectar esas fuentes. La evidencia literal actual no prueba disponibilidad previa para LIVE.</li><li>Autorizar por separado cualquier migración, carga real o despliegue posterior.</li></ol>'
    doc += '<h2>Proceso preparado</h2><p>Captura CSV estricta; IDs y UPC como texto; piezas decimales sin factores. Preflight offline exige fuente y mapeos APPROVED con evidencia, responsable y fecha; valida catálogo activo, scope, corte, versión, tiendas, duplicados y cantidades. Bloquea todo el lote al primer problema de identidad o ante cualquier fila inválida. No existe escritor de producción. Consolidado por cuenta/cadena/formato/modelo/presentación/código; cobertura producto×tienda explícita y faltantes distintos de cero. Historial de snapshots impide reutilizar versión.</p>'
    doc += '<h2>427 identidades — sin omisiones</h2>'+table(["Identidad", "Cadena / formato", "Producto", "Modelo / tamaño / color", "Candidatos UPC", "Señales formato", "Estado", "Candidatos (scope / ID)"], [[r["id"],r["chain"]+" / "+r["format"],r["description"]," / ".join([r["model"],r["size"],r["color"]]),r["candidate_count"],r["format_hint_count"],r["candidate_status"]+" · BLOCKED","; ".join(c["scope"]+" / "+c["product_id"] for c in r["candidates"])] for r in rows])
    doc += '<p>Pruebas y estado final de código: ver pruebas_y_cierre.json e integridad_versiones.json en esta misma carpeta. Reporte privado; no subir a GitHub.</p></html>'
    with (args.output/"Informe_PRD_3A_3.html").open("x", encoding="utf-8") as out: out.write(doc)
    print(json.dumps({**counts, "identities": len(rows), "groups": len(groups), "one_format_hint": summary["one_format_hint"]}))


if __name__ == "__main__": main()
