"""Read and promotion guards for the isolated release rehearsal."""
from __future__ import annotations

from .local import REPO, LocalLab


class UnificationLab(LocalLab):
    def install(self):
        """The migration itself rejects any target except a marked loopback lab."""
        if not self.config["database"].startswith("gateb_unify_"):
            raise ValueError("STOP_UNIFICATION_LAB")
        proposals = sorted((REPO / "lab/unification/supabase/migrations").glob("*.sql"))
        if not proposals:
            raise ValueError("unification_proposal_missing")
        with self.connect() as c, c.transaction():
            for proposal in proposals:
                c.execute(proposal.read_text(encoding="utf-8"))

    def lock_active(self, c, context):
        self.checked(c)
        c.execute("select private.unification_lock_active(%s,%s)", (context.version_id, context.generation))

    def _require_pinned(self, c, context):
        self.checked(c)
        if not c.execute(
            "select id from operational_data_versions where id=%s and manifest_hash=%s and sealed",
            (context.version_id, context.manifest_hash),
        ).fetchone():
            raise ValueError("unavailable_pinned_version")

    def read(self, c, context):
        self._require_pinned(c, context)
        return super().read(c, context)

    def inventory(self, c, context):
        self._require_pinned(c, context)
        return c.execute("""
            select chain_id::text, product_id::text, to_char(period,'YYYY-MM') period,
              format,upc,purchase_type,quantity::text
            from operational_data_version_inventory where version_id=%s
            order by chain_id,product_id,period
        """, (context.version_id,)).fetchall()

    def fill_rate(self, c, context):
        self._require_pinned(c, context)
        return c.execute("""
            select chain_id::text,to_char(period,'YYYY-MM') period,delivery,ordered,fill_rate
            from private.unification_fill_rate(%s) order by chain_id,period
        """, (context.version_id,)).fetchall()
