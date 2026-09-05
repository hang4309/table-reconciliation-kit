"""Run the synthetic equal-totals example and independently check its result."""
from pathlib import Path
from datetime import datetime, timezone
import argparse, csv, hashlib, json, os, subprocess, sys

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args()
    root=Path(__file__).resolve().parent
    case=root/'examples/equal-totals'
    output=(args.output or root/'runs'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')).resolve()
    if output.exists(): parser.error('Output must be a new directory; existing results will not be overwritten.')
    inputs=[case/n for n in ('wms.csv','erp_stock.json','config.json')]
    before={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    env=os.environ.copy();env['PYTHONDONTWRITEBYTECODE']='1'
    env['PYTHONUTF8']='1';env['PYTHONPATH']=str(root)
    def call(*parts):
        result=subprocess.run([sys.executable,'-B','-m','dqrdesk',*parts],cwd=root,env=env,capture_output=True,text=True,encoding='utf-8',timeout=60)
        if result.returncode: raise RuntimeError(result.stderr or result.stdout)
        return json.loads(result.stdout)
    call('run','--config',str(case/'config.json'),'--output',str(output))
    call('verify','--run',str(output))
    status=call('status','--run',str(output))
    expected={'matched':1,'conflict':1,'unmatched':2,'ambiguous':0}
    if status['summary']['status_counts']!=expected: raise AssertionError(status['summary']['status_counts'])
    with (output/'reports/entities.csv').open(encoding='utf-8-sig',newline='') as handle:
        entities=list(csv.DictReader(handle))
    by_sku={r['sku']:r['status'] for r in entities}
    if by_sku!={'A001':'matched','B002':'conflict','C003':'unmatched','D004':'unmatched'}: raise AssertionError(by_sku)
    after={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs}
    if before!=after: raise AssertionError('Example inputs changed')
    print(json.dumps({'example':'synthetic equal totals','checks':'passed','source_rows':6,'entity_status':by_sku,'inputs_unchanged':True,'report':str(output/'reports/report.html')},indent=2))

if __name__=='__main__': main()
