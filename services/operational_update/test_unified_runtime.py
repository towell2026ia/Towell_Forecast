"""Synthetic identity isolation and engine-target preparation checks."""
from __future__ import annotations

from copy import deepcopy
import unittest

from services.operational_update.unified_runtime import bind_catalog,commercial_attributes,normalized_records,uid


def inputs():
    scope={"code":"Synthetic Chain::FORM","parent_uuid":"parent","parent_id":"parent-ref","level":"COMMERCIAL_UNIT"}
    snapshot={"profiles":[{"chain_id":"chain","mapping_json":{"scope":scope}}],
        "chains":[{"id":"parent","name":"Synthetic Chain"}],
        "products":[{"id":"existing","chain_id":"chain","product_code":"000123","status":"ACTIVE","category_id":None}]}
    previous={"catalog":[{"chain_id":"chain","product_id":"existing","account":"A1"}]}
    raw={"clave":"A1|FORM|000123","cuenta":"A1","cliente":"Synthetic Legal Client","formato":"FORM","upc":"000123",
        "modelo":"TOALLA MB SYNTHETIC MODEL","presentacion":"MB","color":"VARIOS","cod_ax":"GROUP","descripcion":"Synthetic product","tiene_historico":True}
    document={"catalog":[raw],"source":{"sha256":"a"*64,"name":"Synthetic unified.xlsx"},"closure":{"last_closed_period":"2026-09"},"rows":[]}
    return document,snapshot,previous


class UnifiedRuntimeTests(unittest.TestCase):
    def test_account_and_upc_keep_existing_uuid(self):
        doc,snapshot,previous=inputs()
        bound=bind_catalog(doc,snapshot,previous)
        self.assertEqual(bound["identities"][0]["product_id"],"existing")
        attrs=bound["identities"][0]["attributes"]
        self.assertEqual(attrs["model"],"SYNTHETIC MODEL");self.assertEqual(attrs["presentation"],"MEDIO BAÑO")
        self.assertEqual(attrs["color"],"VARIOS")
        self.assertEqual(attrs["source_native_attributes"]["model"],"TOALLA MB SYNTHETIC MODEL")

    def test_new_upc_creates_source_uuid_not_old_exclusion(self):
        doc,snapshot,previous=inputs()
        raw=deepcopy(doc["catalog"][0]);raw.update(clave="A1|FORM|000124",upc="000124")
        doc["catalog"].append(raw)
        bound=bind_catalog(doc,snapshot,previous)
        added=bound["identities"][1]
        self.assertEqual(added["state"],"NEW_LOCAL_SOURCE_IDENTITY")
        self.assertEqual(added["product_id"],uid("product:"+raw["clave"]))
        self.assertEqual(bound,bind_catalog(doc,snapshot,previous))

    def test_format_without_accredited_parent_cannot_choose_chain(self):
        doc,snapshot,previous=inputs()
        doc["catalog"][0].update(clave="OTHER|FORM|000123",cuenta="OTHER",cliente="Different legal company")
        bound=bind_catalog(doc,snapshot,previous)
        self.assertEqual(bound["identities"][0]["state"],"PENDING_SCOPE")
        self.assertNotIn("product_id",bound["identities"][0])

    def test_real_order_only_and_october_excluded_from_engine(self):
        doc,snapshot,previous=inputs()
        base={**doc["catalog"][0],"pedido_real_piezas":"0","venta_towell_real_piezas":"91.25","venta_cliente_real_piezas":"101.5",
            "apto_entrenamiento_pedido":True,"periodo":"2026-09", "lineage":{field:{"cell":"Base_2024_2026!"+column+"2"}
            for field,column in [("pedido_real_piezas","L"),("venta_towell_real_piezas","M"),("venta_cliente_real_piezas","N")]}}
        doc["rows"]=[base,{**base,"periodo":"2026-10","pedido_real_piezas":"99","apto_entrenamiento_pedido":False}]
        r=normalized_records(doc,bind_catalog(doc,snapshot,previous))
        self.assertEqual(len(r["facts"]),6)
        self.assertEqual({f["metric_code"] for f in r["facts"]},{"ORDER","SALES","SALES_CLIENTE"})
        self.assertEqual(len(r["engine_records"]),1)
        self.assertEqual(r["engine_records"][0]["value"],"0")
        self.assertEqual(r["engine_records"][0]["period"],"2026-09")
        self.assertEqual(r["engine_records"][0]["metric_code"],"ORDER")
        self.assertEqual(r["engine_records"][0]["source_reference"],"Base_2024_2026!L2")


if __name__ == "__main__":unittest.main()
