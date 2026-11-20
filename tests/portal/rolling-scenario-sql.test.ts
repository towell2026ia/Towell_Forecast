import { PGlite } from "@electric-sql/pglite";
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

const actor = "00000000-0000-4000-8000-000000000001";
const otherActor = "00000000-0000-4000-8000-000000000002";
const chain = "00000000-0000-4000-8000-000000000011";
const otherChain = "00000000-0000-4000-8000-000000000012";
const product = "00000000-0000-4000-8000-000000000021";
const foreign = "00000000-0000-4000-8000-000000000022";
const run = "00000000-0000-4000-8000-000000000031";
const ctx = { source_hash: "a".repeat(64), generation: 8, forecast_contract: "pedido-unificado-v1", objective: "Pedido",
  target_metric: "ORDER", planning_basis: "OBSERVED_AND_COMMERCIAL_CLOSE", scenario_hash: "b".repeat(64),
  observed_cutoff: "2027-02", training_cutoff: "2027-04" };

async function fixture() {
  const db = new PGlite();
  await db.exec(`create role anon; create role authenticated; create role service_role;
    create schema private; create schema auth;
    create function auth.uid() returns uuid language sql stable as
      $$select nullif(current_setting('request.jwt.claim.sub',true),'')::uuid$$;
    create table public.profiles(id uuid primary key,status text);
    create table public.user_chain_access(user_id uuid,chain_id uuid,can_view boolean);
    create function private.can_chain(p_chain uuid,p_action text) returns boolean language sql stable as
      $$select exists(select 1 from public.user_chain_access a join public.profiles p on p.id=a.user_id
        where a.user_id=auth.uid() and a.chain_id=p_chain and a.can_view and p.status='ACTIVE')$$;
    create table private.ft_g6_release(run_id uuid primary key,context jsonb,metadata jsonb,file_sha256 text,content_digest text,active boolean);
    create table private.ft_g6_product(run_id uuid,chain_id uuid,product_id uuid,payload jsonb);
    create table private.fixture_catalog(chain_id uuid,product_id uuid,purchase_type text);
    alter table private.ft_g6_release enable row level security;
    alter table private.ft_g6_product enable row level security;
    alter table private.fixture_catalog enable row level security;
    revoke all on all tables in schema private from public,anon,authenticated,service_role;
    create function public.operational_read_catalog(p_hash text,p_generation bigint)
      returns table(chain_id uuid,product_id uuid,purchase_type text) language sql stable security invoker
      as $$select chain_id,product_id,purchase_type from private.fixture_catalog$$;
    insert into public.profiles values('${actor}','ACTIVE'),('${otherActor}','ACTIVE');
    insert into public.user_chain_access values('${actor}','${chain}',true),('${otherActor}','${otherChain}',true);
    insert into private.fixture_catalog values('${chain}','${product}','RS'),('${otherChain}','${foreign}','RS');`);
  await db.query("insert into private.ft_g6_release values($1,$2,'{}',$3,$4,true)", [run, JSON.stringify(ctx), "c".repeat(64), "d".repeat(64)]);
  for (const [id, scope] of [[product, chain], [foreign, otherChain]]) {
    const payload = { product_id: id, chain_id: scope, candidates: { private: "NOT_EXPOSED" }, input_snapshot: { source_context: ctx,
      commercial_history: [{ period: "2027-03", value: "15.25", basis: "COMMERCIAL_CLOSE", source_reference: "synthetic-own",
        private_peers: [foreign], source_id: "synthetic-source" }], private_evidence: "FOREIGN_SECRET" } };
    await db.query("insert into private.ft_g6_product values($1,$2,$3,$4)", [run, scope, id, JSON.stringify(payload)]);
  }
  await db.exec(readFileSync("scripts/publication_12m/rolling_scenario_read.sql", "utf8"));
  await db.exec(`set role authenticated; select set_config('request.jwt.claim.sub','${actor}',false);`);
  return db;
}

const query = "select public.operational_read_rolling_scenario($1,$2,$3::uuid[],$4::uuid[]) result";
describe("prepared rolling scenario RPC in isolated PostgreSQL", () => {
  it("returns only exact authorised membership and whitelisted source cells", async () => {
    const db = await fixture();
    try {
      const response = await db.query<{ result: { products: { product_id: string }[]; source_context: unknown } }>(query,
        [ctx.source_hash, 8, [product], [chain]]);
      expect(response.rows[0].result.products.map(p => p.product_id)).toEqual([product]);
      expect(response.rows[0].result.source_context).toEqual(ctx);
      const encoded = JSON.stringify(response.rows[0]);
      expect(encoded).not.toContain(foreign);
      expect(encoded).not.toContain("FOREIGN_SECRET");
      expect(encoded).not.toContain("private_peers");
      expect(encoded).not.toContain("candidates");
      expect(encoded).toContain("15.25");
      await expect(db.query("select * from private.ft_g6_product")).rejects.toThrow(/permission denied/);
    } finally { await db.close(); }
  });
  it("rejects foreign chains, wrong product/chain pairs, stale pins and original G6", async () => {
    const db = await fixture();
    try {
      for (const args of [[ctx.source_hash, 8, [foreign], [otherChain]], [ctx.source_hash, 8, [foreign], [chain]],
        ["f".repeat(64), 8, [product], [chain]], [ctx.source_hash, 6, [product], [chain]]]) {
        await expect(db.query(query, args)).rejects.toThrow(/SCOPE_FORBIDDEN|OFFICIAL_SOURCE_MISMATCH|REQUEST_001/);
      }
      await db.exec("reset role; update public.profiles set status='INACTIVE'; set role authenticated;");
      await expect(db.query(query, [ctx.source_hash, 8, [product], [chain]])).rejects.toThrow(/AUTH_REQUIRED/);
    } finally { await db.close(); }
  });
  it("denies anonymous and service role execution and does not grant a writer", async () => {
    const db = await fixture();
    try {
      await db.exec("reset role; set role anon;");
      await expect(db.query(query, [ctx.source_hash, 8, [product], [chain]])).rejects.toThrow(/permission denied/);
      await db.exec("reset role; set role service_role;");
      await expect(db.query(query, [ctx.source_hash, 8, [product], [chain]])).rejects.toThrow(/permission denied/);
    } finally { await db.close(); }
  });
});
