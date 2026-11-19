"""Synthetic single-master contract regression; no commercial quantities."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest

from openpyxl import Workbook

from services.operational_update.unified_source import (MASTER_NAME, SHEETS, UnifiedSourceError,
    boolean, period, read_unified_source)


BASE = ("clave cuenta cliente formato upc modelo presentacion color periodo anio mes pedido_real_piezas venta_towell_real_piezas venta_cliente_real_piezas propuesta_pedidos_piezas propuesta_towell_piezas propuesta_cliente_piezas definitiva_piezas referencia_comercial_piezas pedido_con_dato venta_towell_con_dato venta_cliente_con_dato referencia_con_dato definitiva_con_dato estado_periodo apto_entrenamiento_pedido origen_referencia fuente_historial pedido_celda venta_towell_celda venta_cliente_celda fuente_referencia celda_referencia tipo_referencia champion_adoptado").split()
CAT = "clave cuenta cliente formato upc modelo presentacion color descripcion cod_ax tiene_historico meses_definitiva".split()
METHOD = "clave cuenta cliente formato upc modelo presentacion color descripcion cod_ax periodo origen empaque movil_original suavizamiento_original regresion_original suavizacion_original propuesta_paquetes propuesta_piezas unidad_metodos fuente hoja fila calculo_origen".split()
DEFS = "clave cuenta cliente formato upc modelo presentacion color descripcion cod_ax periodo piezas fuente hoja celda mes_celda tipo_dato emision_conocida evidencia_adicional".split()


def fixture(path, *, override=None, formula=False, duplicate=False):
    wb = Workbook(); wb.remove(wb.active)
    for name in SHEETS: wb.create_sheet(name)
    common = dict(clave="A1|FORM|000123", cuenta="A1", cliente="Synthetic Client", formato="FORM", upc="000123",
                  modelo="TOALLA MB MODELO SINTETICO", presentacion="MB", color="SYNTHETIC", descripcion="Synthetic", cod_ax="S1")
    base = {**common, "periodo": "2026-01-01 00:00:00", "anio": 2026, "mes": 1,
            "pedido_real_piezas": 0, "venta_towell_real_piezas": 1.25, "venta_cliente_real_piezas": None,
            "propuesta_pedidos_piezas": 90, "definitiva_piezas": 0, "referencia_comercial_piezas": 0,
            "pedido_con_dato": "1", "venta_towell_con_dato": True, "venta_cliente_con_dato": "False",
            "referencia_con_dato": 1, "definitiva_con_dato": 1, "estado_periodo": "CLOSED",
            "apto_entrenamiento_pedido": "true", "champion_adoptado": "False"}
    base.update(override or {})
    if formula: base["referencia_comercial_piezas"] = '=IF(ISNUMBER(R2),R2,O2)'
    for sheet, fields, rows in (("Catalogo", CAT, [{**common,"tiene_historico":1}]),
          ("Base_2024_2026", BASE, [base,base] if duplicate else [base]),
          ("Metodos_2026", METHOD, [{**common,"periodo":"2026-01","empaque":8,"unidad_metodos":"ORIGINAL",**{f:7 for f in METHOD[13:19]}}]),
          ("Definitivas", DEFS, [{**common,"periodo":"2026-01","piezas":0,"emision_conocida":"False"}])):
        wb[sheet].append(fields)
        for row in rows: wb[sheet].append([row.get(f) for f in fields])
    wb.save(path)
    return hashlib.sha256(path.read_bytes()).hexdigest()


class UnifiedReaderTests(unittest.TestCase):
    def test_zero_blank_decimal_and_source_lineage(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/MASTER_NAME; sha=fixture(path)
            doc=read_unified_source(path,approved_sha256=sha); row=doc["rows"][0]
            self.assertEqual(row["pedido_real_piezas"],"0"); self.assertEqual(row["venta_towell_real_piezas"],"1.25")
            self.assertIsNone(row["venta_cliente_real_piezas"]); self.assertEqual(row["referencia_comercial_piezas"],"0")
            self.assertEqual(row["lineage"]["pedido_real_piezas"]["cell"],"Base_2024_2026!L2")
            self.assertTrue(doc["source"]["unchanged_after_read"]); self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),sha)
            self.assertEqual(doc["methods"][0]["empaque"],"8")

    def test_boolean_and_period_representations(self):
        for value in [True,1,"1","true","True"]: self.assertTrue(boolean(value))
        for value in [False,0,"0","false","False"]: self.assertFalse(boolean(value))
        self.assertEqual(period(46023),"2026-01")
        self.assertEqual(period("2026-01-01 00:00:00",2026,1),"2026-01")
        with self.assertRaises(UnifiedSourceError): period("2026-01",2025,1)

    def test_missing_master_never_uses_another_book(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/MASTER_NAME
            (Path(directory)/"Other.xlsx").write_bytes(b"not authorized")
            with self.assertRaises(FileNotFoundError): read_unified_source(path)
            sha=fixture(path)
            with self.assertRaisesRegex(UnifiedSourceError,"SHA_MISMATCH"): read_unified_source(path,approved_sha256="0"*64)
            self.assertNotEqual(sha,"0"*64)

    def test_cached_or_derived_definitive_zero_is_authoritative(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/MASTER_NAME; sha=fixture(path,formula=True)
            doc=read_unified_source(path,approved_sha256=sha)
            self.assertEqual(doc["rows"][0]["referencia_comercial_piezas"],"0")
            self.assertTrue(doc["rows"][0]["reference_derived"])

    def test_october_not_trainable_and_duplicate_not_merged(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/MASTER_NAME
            sha=fixture(path,override={"periodo":"2026-10","mes":10})
            with self.assertRaisesRegex(UnifiedSourceError,"TRAINING_ELIGIBILITY"): read_unified_source(path,approved_sha256=sha)
            sha=fixture(path,duplicate=True)
            with self.assertRaisesRegex(UnifiedSourceError,"DUPLICATE"): read_unified_source(path,approved_sha256=sha)

    def test_exact_hash_allows_a_renamed_workbook_but_no_arbitrary_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            original=Path(directory)/MASTER_NAME; sha=fixture(original)
            renamed=Path(directory)/"renamed-authorized.xlsx"
            renamed.write_bytes(original.read_bytes())
            self.assertEqual(read_unified_source(renamed,approved_sha256=sha)["source"]["sha256"],sha)
            renamed.write_bytes(b"unaccredited content")
            with self.assertRaisesRegex(UnifiedSourceError,"SHA_MISMATCH"):
                read_unified_source(renamed,approved_sha256=sha)

    def test_reference_does_not_fall_back_to_other_origins(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/MASTER_NAME
            sha=fixture(path,override={"definitiva_piezas":None,"definitiva_con_dato":0,"propuesta_pedidos_piezas":None,
                "propuesta_towell_piezas":888,"propuesta_cliente_piezas":999,"referencia_comercial_piezas":None,"referencia_con_dato":0})
            row=read_unified_source(path,approved_sha256=sha)["rows"][0]
            self.assertIsNone(row["referencia_comercial_piezas"])


if __name__ == "__main__": unittest.main()
