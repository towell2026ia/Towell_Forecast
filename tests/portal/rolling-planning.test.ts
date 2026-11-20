import { expect, it, vi } from "vitest";
import type { PreviewClient } from "../../lib/forecast-preview";
import type { ReportSnapshot } from "../../lib/forecast-reporting";
import type { OperationalReadContext } from "../../lib/supabase/types";
import { checkPlanningRun, readPlanningReport, type PlanningRun } from "../../lib/rolling-planning";

const id = "77777777-7777-4777-8777-777777777777";
const source = {source_hash:"a".repeat(64),generation:7} as OperationalReadContext;
const run: PlanningRun = {run_id:id,status:"READY",created_at:"2027-03-01T00:00:00Z",issue_period:"2027-02",window_start:"2027-03",window_end:"2028-02",source_hash:source.source_hash,generation:7};
const products = [{id:"p",chain_id:"c"},{id:"other",chain_id:"c"}];
const base = { products, sourceContext:source, decisions:[{}],live:[{}],calculations:[{}],horizons:[{}],provisionals:[{}] } as unknown as ReportSnapshot;
const result = { run_id:id, window_start:run.window_start,window_end:run.window_end,source_context:source,
  products:[{product_id:"p",chain_id:"c"}],calculations:[],horizons:[],provisionals:[] };
const client = (changes={}) => ({planningRun:vi.fn(async()=>run),planningReport:vi.fn(async()=>({...result,...changes}))}) as unknown as PreviewClient;

it("uses the original authorized selection as denominator and clears every G6 forecast channel",async()=>{
  const api = client(), snapshot = await readPlanningReport(base,id,api);
  expect(snapshot.products).toEqual(products);
  expect(snapshot.planningRun).toEqual(run);
  for (const values of [snapshot.calculations,snapshot.horizons,snapshot.provisionals,snapshot.decisions,snapshot.live]) expect(values).toEqual([]);
  expect(api.planningReport).toHaveBeenCalledWith(id,{source_context:source,chain_ids:["c"],product_ids:["other","p"]});
});
it("rejects another version, unknown members and horizons without an accredited calculation",async()=>{
  for (const changes of [{run_id:"other"},{products:[{product_id:"unknown",chain_id:"c"}]},
    {horizons:[{calculation_id:"prior",product_id:"p",chain_id:"c",target_period:"2027-03",horizon:1}]}])
    await expect(readPlanningReport(base,id,client(changes))).rejects.toThrow();
  expect(()=>checkPlanningRun({...run,window_start:"2027-01"},source)).toThrow();
  expect(()=>checkPlanningRun({...run,generation:6},source)).toThrow();
});
