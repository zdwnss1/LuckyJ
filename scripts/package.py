"""Package source, raw corpus, database and receipts without Git metadata or caches."""
import argparse
import hashlib
import json
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parents[1]
ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('--data', type=Path, default=root/'data')
ap.add_argument('--out', type=Path, default=root/'dist')
args = ap.parse_args()
report = json.loads((args.data/'build-report.json').read_text())
if not report['all_linked_games_indexed'] or report['sqlite_integrity'] != 'ok':
    raise SystemExit('Refusing to publish a complete-linked-corpus bundle from an incomplete build')
args.out.mkdir(parents=True, exist_ok=True)
output = args.out/'LuckyJ-phase1.zip'
with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
    for name in ('luckyj.py', 'README.md', 'web', 'scripts', 'tests'):
        path = root/name
        files = path.rglob('*') if path.is_dir() else [path]
        for file in files:
            if file.is_file() and '__pycache__' not in file.parts:
                archive.write(file, 'LuckyJ/'+str(file.relative_to(root)))
    for file in sorted(args.data.rglob('*')):
        if file.is_file() and file.suffix not in ('.tmp', '.building', '.corrupt'):
            archive.write(file, 'LuckyJ/data/'+str(file.relative_to(args.data)))
digest = hashlib.sha256(output.read_bytes()).hexdigest()
(args.out/'SHA256SUMS.txt').write_text(digest+'  '+output.name+'\n')
print(output, output.stat().st_size, digest)
