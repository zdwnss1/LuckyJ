"""Package a verified corpus together with its exact runnable source tree."""
from pathlib import Path
import hashlib
import json
import zipfile

root=Path(__file__).resolve().parent.parent
data=root/'data'
report=json.loads((data/'index-report.json').read_text())
if not report['all_linked_logs_indexed']:
    raise SystemExit('Refusing a complete-data package: some linked logs are not indexed')
out=root/'release';out.mkdir(exist_ok=True)
archive=out/'LuckyJ-phase1.zip'
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    paths=[root/'README.md',root/'requirements.txt']
    for folder in ('luckyj','scripts','tests','docs','licenses','examples','models','data'):
        paths.extend(p for p in (root/folder).rglob('*') if p.is_file() and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.tmp','.so','.dll','.dylib') and '.build-' not in p.name and not p.name.startswith('research-metrics.sqlite'))
    for p in sorted(paths):
        z.write(p,Path('LuckyJ')/p.relative_to(root))
sha=hashlib.sha256(archive.read_bytes()).hexdigest()
(out/'SHA256SUMS.txt').write_text(sha+'  '+archive.name+'\n')
for name in ('index-report.json','download-report.json'):
    if (data/name).exists():(out/name).write_bytes((data/name).read_bytes())
(out/'release-notes.md').write_text(
    f"## LuckyJ 第一阶段数据快照\n\n"
    f"来源记录 {report['source_records']} 条；带链接 {report['unique_linked_logs']} 场全部入库；"
    f"另有 {report['records_without_url']} 条源站无链接记录，保留在报告中但不计入检索。\n\n"
    f"{report['rounds']} 局，{report['decisions']} 次切牌前快照。"
    f"解析错误 {report['index_error_count']} 场。范围仅限清单快照，不代表该账号全部历史对局。\n\n"
    "下载 LuckyJ-phase1.zip，解压后进入 LuckyJ，运行 `python -m luckyj serve --data data`，"
    "浏览器打开 http://127.0.0.1:8000。需要 Python 3.10+，无需第三方运行依赖。"
    "包含完整原谱、SQLite、覆盖报告、源代码及测试。请保留 SHA256SUMS.txt 校验完整性。\n",
    encoding='utf8')
print(archive,archive.stat().st_size,sha)
