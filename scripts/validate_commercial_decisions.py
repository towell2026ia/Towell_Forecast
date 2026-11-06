"""Validate edited private review workbook; write a local decision preview only."""
import argparse
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from services.inventory_preflight.commercial_decisions import validate

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('xlsx','baseline','current','output'):p.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args();load=lambda p:json.loads(p.read_text(encoding='utf-8-sig'))
    result=validate(a.xlsx,load(a.baseline)['rows'],load(a.current)['products'])
    with a.output.open('x',encoding='utf-8') as f:json.dump(result,f,ensure_ascii=False,indent=2,default=str)
    print({k:len(result[k]) for k in ('approved','rejected','pending','errors')},result['status'])
    return 0 if not result['errors'] else 2
if __name__=='__main__':raise SystemExit(main())
