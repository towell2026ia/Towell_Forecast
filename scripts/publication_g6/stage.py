"""Stage already verified private files in the explicitly linked project. Does not activate a release."""
import argparse
import hashlib
import json
import re
import subprocess
import time
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import UUID


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, type=Path, help="Existing linked Supabase checkout")
    parser.add_argument("--project-ref", required=True)
    parser.add_argument("--batches", type=Path, required=True)
    parser.add_argument("--archive-sha", required=True)
    parser.add_argument("--manifest-sha", required=True)
    args = parser.parse_args()
    checkout = args.repo.resolve()
    if (checkout/"supabase/.temp/project-ref").read_text().strip() != args.project_ref:
        raise SystemExit("Linked project mismatch")
    windows_exe = checkout/"node_modules/@supabase/cli-windows-x64/bin/supabase.exe"
    cli = windows_exe if windows_exe.is_file() else checkout/"node_modules/.bin/supabase"
    data = json.loads((args.batches/"batches.json").read_bytes())
    if data["manifest_sha256"] != args.manifest_sha or data["archive_sha256"] != args.archive_sha:
        raise SystemExit("Snapshot manifest or archive does not match requested publication")
    def apply_file(path):
        for retry in range(5):
            try:
                result = subprocess.run([str(cli),"db","query","--linked","--output","json","--file",str(path.resolve())],
                                        cwd=checkout,stdin=subprocess.DEVNULL,capture_output=True,timeout=150)
            except subprocess.TimeoutExpired:
                time.sleep(2**retry)
                continue
            if result.returncode == 0:
                return result.stdout
            # Never echo query bodies or authentication diagnostics to the console.
            reason = result.stderr.decode(errors="replace").casefold()
            if any(word in reason for word in ("429","502","503","504","too many","timed out","timeout","reset by peer")):
                time.sleep(2**retry)
                continue
            codes = re.findall(r"\b(?:sqlstate\s*[:=]?\s*|error\s+)([a-z0-9]{5})\b",reason)
            # Whitelist server diagnostic categories: private source data never goes to stdout.
            categories = [s for s in ("permission denied","does not exist","duplicate key","authentication",
                "syntax error","exceeds","too large","connection refused","unrecognized","login role",
                "forbidden","unauthorized","cannot","encoding","mismatch") if s in reason]
            raise RuntimeError(f"{path.name} failed: exit={result.returncode} SQLSTATE={codes}, categories={categories}")
        raise RuntimeError(f"{path.name} failed after bounded retries")
    apply_file(args.batches/"000_release.sql")
    def batch_bytes(entry):
        path = (args.batches/entry["file"]).resolve()
        if not path.is_relative_to(args.batches.resolve()):
            raise ValueError("Batch path escaped private folder")
        raw=path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError("Prepared batch changed")
        return raw
    with NamedTemporaryFile(dir=args.batches,suffix=".sql",delete=False) as stream:
        probe=Path(stream.name)
        stream.write(("select resource,batch_no,row_count,content_sha256 from private.ft_g6_receipt "
                      f"where run_id='{UUID(data['run_id'])}';").encode())
    try:
        completed={(x["resource"],x["batch_no"]):x for x in json.loads(apply_file(probe))["rows"]}
    finally:
        probe.unlink()
    pending=[]
    for entry in data["batches"]:
        raw=batch_bytes(entry)
        batch_no=int(Path(entry["file"]).stem.rsplit("_",1)[1])
        receipt=completed.get((entry["resource"],batch_no))
        if receipt:
            expected=re.search(rb"expected text:='([a-f0-9]{64})'; body",raw)
            if receipt["content_sha256"]!=expected.group(1).decode() or receipt["row_count"]!=entry["rows"]:
                raise ValueError("Existing receipt disagrees with sealed transport")
        else:
            pending.append(entry)
    # One CLI login at a time. Parallel temporary login-role rotations can invalidate
    # each other's connections. Each group is transactional and can be replayed.
    print(f"Verified {len(completed)} existing receipts; {len(pending)} pending",flush=True)
    # Two 760 kB UTF-8 chunks fit within the hosted SQL request limit.
    for offset in range(0,len(pending),2):
        group=pending[offset:offset+2]
        with NamedTemporaryFile(dir=args.batches,suffix=".sql",delete=False) as stream:
            current=Path(stream.name)
            stream.write(b"begin;\n")
            for item in group:
                stream.write(batch_bytes(item))
            stream.write(b"\ncommit;\n")
        try:
            apply_file(current)
        finally:
            current.unlink()
        print(f"Staged {min(offset+len(group),len(pending))}/{len(pending)} outstanding receipts",flush=True)


if __name__ == "__main__":
    main()
