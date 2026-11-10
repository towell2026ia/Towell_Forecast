"""Render reviewed SQL phases without connecting to any database."""
from __future__ import annotations

import argparse
import re
from pathlib import Path

from .local import REPO


CUTOVER = REPO / "lab/unification/cutover"
GATEB = REPO / "lab/prd3a8/supabase/migrations"
UNIFICATION = REPO / "lab/unification/supabase/migrations"

GUARD = """
create function private.operational_assert_maintenance() returns void
language plpgsql stable set search_path='' as $$
begin
  if session_user is distinct from (
       select pg_get_userbyid(c.relowner) from pg_catalog.pg_class c
       where c.oid='public.monthly_observations'::regclass
     ) or current_setting('towell.cutover_database',true) is distinct from current_database()
     or not coalesce(current_setting('towell.cutover_manifest_sha256',true),'') ~ '^[0-9a-f]{64}$'
     or not (
       coalesce(current_setting('towell.cutover_mode',true),'')='REVIEWED_MAINTENANCE'
       or (
         coalesce(current_setting('towell.cutover_mode',true),'')='SYNTHETIC_REVIEW'
         and current_database() ~ '^gateb_unify_'
         and host(inet_server_addr()) is not distinct from '127.0.0.1'
         and coalesce(current_setting('gateb.lab_id',true),'') ~ '^[0-9a-f]{32}$'
       )
     ) then raise exception 'STOP_OPERATIONAL_CUTOVER_GUARD' using errcode='42501'; end if;
end $$;
revoke all on function private.operational_assert_maintenance() from public,anon,authenticated,service_role;
"""


def without_lab_entry_guard(path):
    source = path.read_text(encoding="utf-8")
    match = re.match(r"(?is)^(?:--[^\n]*\n)*do \$\$ begin\b.*?end \$\$;\s*", source)
    if not match or "STOP_" not in match[0]:
        raise ValueError("unrecognized_lab_sql_entry_guard")
    return source[match.end():]


def production_transition(source):
    previous = re.compile(
        r" if host\(inet_server_addr\(\)\)<>'127\.0\.0\.1'.*?raise exception 'STOP_B0'; end if;",
        re.S,
    )
    replaced, count = previous.subn(" perform private.operational_assert_maintenance();", source)
    if count != 1 or " if p_fail then raise exception 'injected_activation_failure'; end if;" not in replaced:
        raise ValueError("unrecognized_lab_transition_guard")
    replaced = replaced.replace(
        " if p_fail then raise exception 'injected_activation_failure'; end if;",
        " if p_fail then raise exception 'test_fault_not_supported'; end if;",
    )
    anchor = " -- Serialize all local writers"
    if replaced.count(anchor) != 1:
        raise ValueError("unrecognized_lab_transition_context")
    return replaced.replace(anchor, """  if v.document->>'archive_manifest_sha256' is distinct from current_setting('towell.cutover_manifest_sha256',true)
    then raise exception 'maintenance_archive_sha_mismatch' using errcode='42501'; end if;
  -- Serialize all local writers""")


def proposed_sql():
    base = sorted(GATEB.glob("*.sql"))
    extension = sorted(UNIFICATION.glob("*.sql"))
    if len(base) != 4 or len(extension) != 1:
        raise ValueError("cutover_sql_dependencies_changed")
    invoke = "select private.operational_assert_maintenance();\n"
    first = [production_transition(without_lab_entry_guard(base[0]))]
    first.extend(without_lab_entry_guard(path) for path in base[1:])
    before_a = (
        "-- Proposal phase 01. Requires a reviewed, frozen baseline and a single transaction.\n"
        + GUARD + invoke + "\n".join(first)
        + "\nrevoke execute on function private.gateb_seal(uuid),"
          "private.gateb_activate(uuid,text,uuid,bigint,text,boolean) from public,anon,authenticated,service_role;\n"
    )
    after_a = (
        "-- Proposal phase 02. Requires sealed and active baseline A.\n" + invoke
        + without_lab_entry_guard(extension[0]) + "\n"
        + (CUTOVER / "02_pinned_consumers.sql").read_text(encoding="utf-8")
    )
    return before_a, after_a


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("before-a", "after-a"))
    args = parser.parse_args()
    print(proposed_sql()[args.phase == "after-a"])


if __name__ == "__main__":
    main()
