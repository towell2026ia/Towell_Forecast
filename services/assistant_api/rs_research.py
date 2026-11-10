"""Public RS context with durable deduplication."""
from __future__ import annotations

import json
import sqlite3
import time
import unicodedata
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from uuid import UUID

from .cold_start_research import PUBLIC_CUTOFF, SAFE_RESPONSE_ID, public_research_result
from .persistence import content_hash

POLICY = "RS-PUBLIC-CONTEXT-1"
METHOD = "PUBLIC_WEB_SEARCH"
MODEL = "gpt-5.5"
SCOPE_FIELDS = frozenset({"chain_id", "format", "model", "period", "version_id"})
PUBLIC_PRODUCT_TYPES = {
    "TOALLA": "TEXTILES_TOALLAS_MX", "TAPETE": "TEXTILES_TAPETES_MX",
    "TRAPO": "TEXTILES_TRAPOS_MX", "BATA": "TEXTILES_BATAS_MX",
}


def _scope_key(scope):
    optional = {"purchase_type", "public_product_type"}
    if not isinstance(scope, dict) or not SCOPE_FIELDS <= scope.keys() <= SCOPE_FIELDS | optional:
        raise ValueError("invalid_research_scope")
    if any(not isinstance(scope[name], str) for name in SCOPE_FIELDS) or scope.get("purchase_type", "RS") != "RS":
        raise ValueError("invalid_research_scope")
    canonical = lambda value: " ".join(unicodedata.normalize("NFKC", value).upper().split())
    fmt, model, period = canonical(scope["format"]), canonical(scope["model"]), scope["period"]
    if not (0 < len(fmt) <= 120 and 0 < len(model) <= 120 and PUBLIC_CUTOFF.fullmatch(period)):
        raise ValueError("invalid_research_scope")
    try:
        chain, version = str(UUID(scope["chain_id"])), str(UUID(scope["version_id"]))
    except ValueError:
        raise ValueError("invalid_research_scope") from None
    declared = scope.get("public_product_type")
    topic = PUBLIC_PRODUCT_TYPES.get(canonical(declared)) if isinstance(declared, str) else None
    key = content_hash({"policy": POLICY, "method": METHOD, "provider_model": MODEL, "purchase_type": "RS",
                        "chain_id": chain, "format": fmt, "model": model, "period": period, "version_id": version,
                        "public_topic": topic})
    return key, topic, period


def _iso(timestamp):
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat()


class RSResearch:
    def __init__(self, storage, researcher, *, max_pending=4, poll_interval=15,
                 poll_ttl=600, max_polls=20, now=time.time):
        limits = (max_pending, poll_interval, poll_ttl, max_polls)
        if any(type(limit) is not int or limit < 1 for limit in limits):
            raise ValueError("invalid_research_limits")
        self.researcher, self.now = researcher, now
        self.max_pending, self.poll_interval = max_pending, poll_interval
        self.poll_ttl, self.max_polls = poll_ttl, max_polls
        if self.researcher is None:
            return
        self.path = storage.path
        with self._transaction() as c:
            c.execute("""create table if not exists rs_public_research (
                cache_key text primary key, started real not null, finished real,
                next_poll real not null, polls integer not null default 0,
                state text not null check(state in ('RESERVED','PENDING','COMPLETED','UNAVAILABLE')),
                provider_id text, result text)""")

    @contextmanager
    def _transaction(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as c, c:
            c.row_factory = sqlite3.Row
            c.execute("begin immediate")
            yield c

    @staticmethod
    def _lookup(c, key):
        return c.execute("select * from rs_public_research where cache_key=?", (key,)).fetchone()

    @staticmethod
    def _public(row):
        public = json.loads(row["result"]) if row["result"] else {"status": "PENDING"}
        return {**public, "method": METHOD, "model": MODEL, "evidence_scope": "PUBLIC_CATEGORY_CONTEXT",
                "source_publication_dates_verified": False, "started_at": _iso(row["started"]),
                "finished_at": _iso(row["finished"]) if row["finished"] is not None else None}

    @staticmethod
    def _finish(c, key, now, result):
        c.execute("""update rs_public_research set state=?,finished=?,provider_id=null,result=?
                    where cache_key=? and state in ('RESERVED','PENDING')""",
                  (result["status"], now, json.dumps(result, ensure_ascii=False), key))

    def _expire(self, c, now):
        c.execute("""update rs_public_research set state='UNAVAILABLE',finished=?,provider_id=null,result=?
            where (state='RESERVED' and next_poll<=?) or (state='PENDING' and started<=?)""",
            (now, json.dumps({"status": "UNAVAILABLE", "reason": "TIMEOUT"}), now, now - self.poll_ttl))

    def start(self, scope):
        """Reserve an available slot atomically before initiating a provider response."""
        key, topic, period = _scope_key(scope)
        if not self.researcher:
            return {"status": "DISABLED"}
        if not topic:
            return {"status": "UNAVAILABLE", "reason": "PUBLIC_TOPIC_UNVERIFIED"}
        now = self.now()
        with self._transaction() as c:
            self._expire(c, now)
            stored = self._lookup(c, key)
            if stored is not None:
                return self._public(stored)
            pending = c.execute("select count(*) from rs_public_research where state in ('RESERVED','PENDING')").fetchone()[0]
            if pending >= self.max_pending:
                return {"status": "UNAVAILABLE", "reason": "BUSY", "retryable": True}
            c.execute("""insert into rs_public_research(cache_key,started,next_poll,state)
                         values(?,?,?,'RESERVED')""", (key, now, now + min(120, self.poll_ttl)))
        try:
            response = self.researcher.start_public(topic, cutoff=period)
        except Exception:
            response = {"status": "UNAVAILABLE", "reason": "PROVIDER_ERROR"}
        now = self.now()
        response_id = response.get("response_id") if isinstance(response, dict) and response.get("status") == "PENDING" else None
        with self._transaction() as c:
            self._expire(c, now)
            if isinstance(response_id, str) and SAFE_RESPONSE_ID.fullmatch(response_id):
                c.execute("""update rs_public_research set state='PENDING',provider_id=?,next_poll=?
                             where cache_key=? and state='RESERVED'""", (response_id, now, key))
            else:
                self._finish(c, key, now, public_research_result(response))
            return self._public(self._lookup(c, key))

    def status(self, scope):
        """Read a saved result, polling a previously reserved response at most on schedule."""
        key, topic, _ = _scope_key(scope)
        if not self.researcher:
            return {"status": "DISABLED"}
        if not topic:
            return {"status": "UNAVAILABLE", "reason": "PUBLIC_TOPIC_UNVERIFIED"}
        now = self.now()
        with self._transaction() as c:
            self._expire(c, now)
            row = self._lookup(c, key)
            if row is None:
                return {"status": "NOT_REQUESTED"}
            if row["state"] in {"UNAVAILABLE", "COMPLETED"}:
                return self._public(row)
            if row["state"] == "RESERVED" or now < row["next_poll"]:
                return self._public(row)
            if row["polls"] >= self.max_polls:
                self._finish(c, key, now, {"status": "UNAVAILABLE", "reason": "POLL_LIMIT"})
                return self._public(self._lookup(c, key))
            c.execute("update rs_public_research set next_poll=?,polls=polls+1 where cache_key=?",
                      (now + max(self.poll_interval, 60), key))
        try:
            response = self.researcher.poll(row["provider_id"])
        except Exception:
            response = {"status": "UNAVAILABLE", "reason": "PROVIDER_ERROR"}
        now = self.now()
        with self._transaction() as c:
            self._expire(c, now)
            if isinstance(response, dict) and response.get("status") == "PENDING":
                if row["polls"] + 1 >= self.max_polls:
                    self._finish(c, key, now, {"status": "UNAVAILABLE", "reason": "POLL_LIMIT"})
                else:
                    c.execute("update rs_public_research set next_poll=? where cache_key=? and state='PENDING'",
                              (now + self.poll_interval, key))
            else:
                self._finish(c, key, now, public_research_result(response))
            return self._public(self._lookup(c, key))

    def cached(self, scope):
        """Return the last saved public result without contacting the provider."""
        key, topic, _ = _scope_key(scope)
        if not self.researcher:
            return {"status": "DISABLED"}
        if not topic:
            return {"status": "UNAVAILABLE", "reason": "PUBLIC_TOPIC_UNVERIFIED"}
        with closing(sqlite3.connect(self.path, timeout=10)) as c:
            c.row_factory = sqlite3.Row
            row = self._lookup(c, key)
            return self._public(row) if row else {"status": "NOT_REQUESTED"}
