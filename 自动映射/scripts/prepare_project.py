from pathlib import Path
import hashlib,json,shutil,datetime
ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT.parent
snap=ROOT/'source_snapshot'
work=ROOT/'working'/'baseline'
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()
if snap.exists() or work.exists(): raise SystemExit("Snapshot already exists; do not overwrite")
files=[]
for p in SOURCE.rglob('*'):
    if not p.is_file() or p.is_relative_to(ROOT) or p.name.startswith('~$'):continue
    if p.is_symlink():raise RuntimeError('Symlink source requires explicit review')
    files.append(p)
snap.mkdir(parents=True);work.mkdir(parents=True)
records=[]
for p in files:
    rel=p.relative_to(SOURCE);before=sha(p)
    for dest in (snap/rel,work/rel):
        dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
        if sha(dest)!=before:raise RuntimeError('Source changed during copy: '+str(rel))
    after=sha(p)
    if after!=before:raise RuntimeError('Source changed: '+str(rel))
    records.append({'relative_path':str(rel),'size':p.stat().st_size,'sha256':before})
data={'created_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'source':str(SOURCE),'snapshot':str(snap),'working':str(work),'files':records}
out=ROOT/'artifacts'/'source_manifest.json';out.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'files':len(records),'bytes':sum(x['size'] for x in records),'working':str(work)},ensure_ascii=False))
