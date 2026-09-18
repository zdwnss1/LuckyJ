# LuckyJ

LuckyJ 天凤牌谱数据库。第一阶段只做**原始数据归档、逐切牌状态重建、具体场况检索和具体切牌检索**；统计特征、打法标签和名词体系留到下一阶段。

目标账号严格使用 `ⓃLuckyJ`，不会自动合并普通同名账号 `LuckyJ`。

## 第一阶段的数据结构

- `data/raw/YYYY/*.xml.gz`：从天凤取得的原始牌谱，gzip 无损归档；不提交到 Git。
- `data/manifest.json` / `data/download-report.json`：来源清单、下载状态与完整性证据。
- `dist/luckyj.sqlite3`：可查询数据库，一行 `decisions` 对应 LuckyJ 的一次切牌。
- `web/` + `app/server.py`：零第三方依赖的检索前端与 API。

数据库保存尽量可逆的原子事实，而不是预先定义“好形/危险牌/押引”等统计概念。当前每次切牌包括：场风、几局、几本场、供托；四家点数、LuckyJ 点数、当前点数順位；庄家与自风；巡目、摸牌、切牌、摸切/手切、立直状态；切牌前副露外手牌（保留赤五）、副露明细；原牌谱 ID 与天凤链接。

> `score_rank` 是查询方便使用的“当前点数順位”：点数相同时用 seat index 稳定打破平手；它不是对最终順位规则的重新定义。

## 下载全部牌谱

```bash
python3 scripts/download.py --data data
```

下载器从 Nodocchi 的精确玩家清单发现牌谱 ID，再从天凤下载 XML；已有文件会先解压并重新校验。HTTP 403/429 时会停止继续请求，不尝试绕过限制。

是否“全量”必须看 `data/download-report.json`，尤其是 `source_records`、`unique_linked_logs`、`records_without_url`、`downloaded`、`failed`、`not_attempted`、`all_linked_logs_downloaded` 和 `all_source_records_have_logs`。

## 建库

```bash
python3 scripts/build_db.py --data data --output dist/luckyj.sqlite3
```

建库会对每个原始 XML 再做精确账号检查和状态重建。任何一场解析失败都会写入 `dist/build-report.json` 并令命令失败，避免悄悄生成残缺数据库。

## 前端

```bash
python3 app/server.py --db dist/luckyj.sqlite3
# http://127.0.0.1:8000
```

可筛选场风、几局、几本场、順位、自风、四家点数、自身点数、巡目、摸牌、切牌、摸切/手切，以及**完整牌姿精确匹配**。

API 示例：

```text
/api/search?round_wind=S&kyoku=3&honba=1&rank=2&discard=0m&tsumogiri=0
```

## 自动归档

`.github/workflows/archive.yml` 会恢复历史 raw cache、继续下载、跑解析测试、建 SQLite、做完整性烟测，并上传：

1. `luckyj-raw-archive`：原牌谱 + manifest + 下载报告；
2. `luckyj-phase1-db`：SQLite + build report + 前端代码。

第二阶段可直接在当前 `decisions` 原子表上增加派生特征，不需要改变原始牌谱格式。
