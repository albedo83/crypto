from pathlib import Path
import sqlite3,hashlib,json
import argparse
parser=argparse.ArgumentParser(description='Read-only coherent snapshots for CASCADE-v1')
parser.add_argument('--source-root',type=Path,required=True)
parser.add_argument('--work-dir',type=Path,required=True)
args=parser.parse_args();root=(args.source_root/'backtests/output').resolve();dest=args.work_dir.resolve()
if dest==root or root in dest.parents:raise ValueError('Snapshot must be outside the source data directory')
dest.mkdir(parents=True,exist_ok=True)
if any((dest/name).exists() for name in ['event_cache.db','oi_history.db','funding_history.db']):
 raise ValueError('Refusing to overwrite an existing snapshot')
manifest={}
for name in ['event_cache.db','oi_history.db','funding_history.db']:
 with sqlite3.connect((root/name).as_uri()+'?mode=ro',uri=True) as source:
  source.execute('PRAGMA query_only=ON')
  with sqlite3.connect(dest/name) as out:source.backup(out)
 manifest[name]=hashlib.sha256((dest/name).read_bytes()).hexdigest()
(dest/'snapshot_hashes.json').write_text(json.dumps(manifest,indent=2)+'\n')
print('Three read-only sources snapshotted and hashed')
