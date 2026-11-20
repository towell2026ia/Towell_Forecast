import { readFileSync } from "node:fs";
import { PGlite } from "@electric-sql/pglite";
import { expect, it } from "vitest";

it("preserves the published G6 check and admits only complete sealed rolling calendars", async () => {
  const db = new PGlite();
  try {
    const old = readFileSync("scripts/publication_g6/01_hosted_reads.sql","utf8").split("create unique index")[0];
    await db.exec("create schema private;" + old);
    const change = readFileSync("scripts/publication_g6/rolling_source_calendar.sql","utf8");
    await db.exec(change);
    await db.exec(change);
    const source = { forecast_contract: "pedido-unificado-v1", objective: "Pedido", target_metric: "ORDER", unit: "PIEZAS",
      generation: 7, observed_cutoff: "2027-02", training_cutoff: "2027-02", first_period: "2024-01", last_period: "2027-02",
      planning_start: "2027-03", planning_end: "2028-02", planning_basis: "OBSERVED_AND_COMMERCIAL_CLOSE",
      target_sheet: "Base_2024_2026", target_file_sha256: "29cac8aebab869e1ded099fa93382046bdbafbaaf4c0156664d52c9488a47c5d",
      source_hash: "a".repeat(64), authorized_source_hash: "a".repeat(64), scenario_hash: "b".repeat(64) };
    const insert = (ctx: Record<string,unknown>) => db.query("insert into private.ft_g6_release(run_id,context,metadata,file_sha256,content_digest) values (gen_random_uuid(),$1,'{}',$2,$2)", [JSON.stringify(ctx),"c".repeat(64)]);
    await insert(source);
    await insert({ ...source, generation: 6, observed_cutoff:"2026-09",training_cutoff:"2026-12",planning_start:"2027-01",planning_end:"2027-12" });
    for (const changed of [{generation:6},{target_metric:"SALES"},{scenario_hash:null},{planning_start:"2027-01"},{observed_cutoff:"2027-04"}])
      await expect(insert({...source,...changed})).rejects.toThrow();
    expect((await db.query("select count(*)::int as n from private.ft_g6_release where active")).rows[0]).toEqual({n:0});
  } finally { await db.close(); }
},30000);
