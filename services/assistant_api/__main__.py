"""Technical local CLI for historical jobs; never exposed through the assistant."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .data_provider import DATA, NormalizedDataProvider
from .historical_runner import HistoricalForecastRunner
from .runner import LocalResearchProvider


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m services.assistant_api")
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--data-dir", type=Path, help="Directory with normalized fendi-engine-series.csv")
    parser.add_argument("--research-dir", type=Path, help="Optional local YYYY-MM.json evidence snapshots")
    commands = parser.add_subparsers(dest="command", required=True)
    audit = commands.add_parser("availability-audit")
    audit.add_argument("--start", default="2023-01")
    audit.add_argument("--end", default="2026-08")
    audit.add_argument("--chain", default="Walmart")
    audit.add_argument("--dry-run", action="store_true")
    backfill = commands.add_parser("availability-backfill")
    backfill.add_argument("--source-dir", type=Path, required=True)
    backfill.add_argument("--apply", action="store_true")
    backfill.add_argument("--dry-run", action="store_true")
    first_valid = commands.add_parser("availability-first-valid")
    first_valid.add_argument("--start", default="2023-01")
    first_valid.add_argument("--end", default="2026-08")
    first_valid.add_argument("--chain", default="Walmart")
    first_vintage = commands.add_parser("availability-first-vintage")
    first_vintage.add_argument("--start", default="2023-01")
    first_vintage.add_argument("--end", default="2026-08")
    first_vintage.add_argument("--force-rerun", action="store_true")
    harden = commands.add_parser("evidence-harden")
    harden.add_argument("--source-dir", type=Path, required=True)
    expansion = commands.add_parser("vintage-expand")
    expansion.add_argument("--source-dir", type=Path, required=True)
    expansion.add_argument("--start", default="2023-01")
    expansion.add_argument("--end", default="2026-08")
    expansion.add_argument("--max-new", type=int, default=3)
    commands.add_parser("vintage-registry")
    historical = commands.add_parser("historical")
    historical.add_argument("--start", required=True)
    historical.add_argument("--end", required=True)
    historical.add_argument("--force-rerun", action="store_true")
    historical.add_argument("--continue-on-error", action="store_true")
    month = commands.add_parser("month")
    month.add_argument("--period", required=True)
    month.add_argument("--force-rerun", action="store_true")
    resume = commands.add_parser("resume")
    resume.add_argument("--run-id", required=True)
    resume.add_argument("--resume-from")
    for name in ("status", "cancel", "pause"):
        sub = commands.add_parser(name)
        sub.add_argument("--run-id", required=True)
    args = parser.parse_args()
    runner = HistoricalForecastRunner(NormalizedDataProvider(data_dir=args.data_dir or DATA),
                                      research=LocalResearchProvider(args.research_dir),
                                      state_dir=args.state_dir)
    if args.command == "availability-audit":
        result = runner.availability.audit_availability(args.start, args.end, chain=args.chain)
    elif args.command == "availability-backfill":
        result = runner.availability.backfill_availability(args.source_dir,
                                                           dry_run=not args.apply)
    elif args.command == "availability-first-valid":
        result = runner.availability.find_first_temporally_valid_period(args.start, args.end,
                                                                        chain=args.chain)
    elif args.command == "availability-first-vintage":
        result = runner.first_vintage(start=args.start, end=args.end,
                                      force_rerun=args.force_rerun)
    elif args.command == "evidence-harden":
        result = runner.harden_evidence(args.source_dir)
    elif args.command == "vintage-expand":
        result = runner.expand_vintages(args.source_dir, args.start, args.end,
                                        max_new=args.max_new)
    elif args.command == "vintage-registry":
        result = runner.vintage_registry.all()
    elif args.command == "historical":
        result = runner.run_range(args.start, args.end, stop_on_error=not args.continue_on_error,
                                  force_rerun=args.force_rerun)
    elif args.command == "month":
        result = runner.run_month(args.period, force_rerun=args.force_rerun)
    elif args.command == "resume":
        result = runner.resume(args.run_id, resume_from=args.resume_from)
    elif args.command == "status":
        result = json.loads((runner.state_dir / "jobs" / f"{args.run_id}.json").read_text(encoding="utf-8"))
    elif args.command == "cancel":
        runner.cancel(args.run_id)
        result = {"run_id": args.run_id, "cancel_requested": True}
    else:
        runner.pause(args.run_id)
        result = {"run_id": args.run_id, "pause_requested": True}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
