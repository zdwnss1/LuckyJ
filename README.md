# LuckyJ · 牌谱检索档案

第一阶段：**原始牌谱归档 → 逐事件重建 → SQLite 切牌索引 → 中文检索前端**。
目标账号精确为 `ⓝLuckyJ`，不自动合并普通同名账号。Python 3.10+，运行无需安装第三方依赖、Node.js 或数据库服务。

## 现在能查什么

场况和切牌条件可以任意组合，全部按 AND 生效。页面的“场况 → 切牌”和“切牌 → 场况”只是两个入口，不是两套不一致的数据。

| 条件 | 支持内容 |
|---|---|
| 局数、本场 | 东南西北各局，具体本场数 |
| 分数、次位 | LuckyJ 次位，自己／下家／对家／上家的分数上下限；上下限相同即精确值 |
| 分数口径 | 默认切牌前，另可选本局开始时；次位随同一口径切换 |
| 具体切牌 | 切出的牌、刚摸到的牌、手出或摸切 |
| 具体手牌 | 包含某组牌并匹配张数，或暗手牌完全相同；含本次摸牌，不含已鸣出的牌 |
| 连续切牌 | 同一局 LuckyJ 的相邻切牌，序列必须结束于本次检索到的切牌 |
| 赤牌 | 默认区分赤五与普通五，可显式合并 |
| 切牌位置 | 本局第几次切牌的上下限；鸣牌后的切牌也计一次，不冒充严格巡目 |

牌型输入：`123m405p77z`，`m/p/s/z` = 万／筒／索／字，`0` = 赤五，`1z–7z` = 东南西北白发中。
例如手牌包含 `4556m`，切牌 `5m`，南四局、4 位、自己 20000 分以下，可以一起检索。连续切牌 `1z9m5p` 不会匹配“曾经出现过但之后又切了别的牌”的后续状态。

详情展示 LuckyJ 暗手牌、刚摸的牌、本次选择、四家分数、此前牌河、已公开副露、立直状态和当时的宝牌指示牌；不显示对手暗手牌，也不泄漏之后的鸣牌或新增宝牌。可打开天凤原谱、下载原始 XML.gz、查看本场全部 LuckyJ 切牌。原谱链接定位到对局与座位；具体局和事件序号另列于详情，不假称原站能精确跳转至该切牌。

不做向听、牌效、押引、攻守、战术名词或统计特征；这些留到下一轮。

## 使用

下载含数据库的完整运行包后，解压进入 `LuckyJ` 目录：

```bash
python luckyj.py serve --data data
```

浏览器打开 **http://127.0.0.1:8765**。Windows 也可使用 `py` 替代 `python`。页面由本机 Python 服务提供，不是双击 HTML，也不是已部署的公网网站。

从仓库源码开始：

```bash
git clone https://github.com/zdwnss1/LuckyJ.git
cd LuckyJ
git switch phase1-database
python scripts/download.py --data data
python luckyj.py build --data data
python luckyj.py serve --data data
```

下载可续跑，遇到 403/429 会停止，不绕过限制。重新 build 不会再次请求源站。也可使用其他路径：`python luckyj.py serve --data /path/to/data --db /path/to/luckyj.sqlite --port 8765`。

默认仅监听回环地址。`--host 0.0.0.0` 只适合可信局域网；本项目的开发服务器没有登录、HTTPS、访问配额或公网部署加固。

## 数据范围与完整性

2026-09-18 的来源探测发现 **1,321 条对局记录，1,256 个唯一牌谱链接，65 条无牌谱链接记录**。这是源清单的两类记录，不是把 65 条下载失败藏起来。实际完成情况始终以运行包中的 `data/download-report.json` 和 `data/build-report.json` 为准。

- 每一条来源记录都进入 `source_records`，包括无链接、未下载和解析失败记录。
- 原始清单按原字节保留；原始 XML 以确定性 gzip 保存，记录未压缩原文的 SHA-256。
- 建库检查清单与下载回执的一致性，并核对有回执的原文哈希。
- `all_linked_games_indexed` 只表示全部带链接的牌谱已入库，**不等于** `complete_source_coverage`。
- 任一对局解析失败，该对局不会部分入库；报告保留原因。建库返回非零退出码，但可检查已通过校验的数据。
- GitHub Actions artifact 有保留期限；应下载归档包保存。打包工作流还可发布独立 Release 资产，避免把大数据库提交到代码历史。

## 存储设计

```text
data/
  manifest.json 或 source-manifest.json  原始来源清单
  manifests/                            历次清单快照
  raw/[年份/]<log_id>.xml.gz             原始事件日志
  download-report.json                  下载状态与哈希
  build-report.json                     索引覆盖与校验结果
  luckyj.sqlite                         可重建的查询库
```

兼容现有下载器的按年目录及首批平铺归档。两种清单文件同时存在时以 `manifest.json` 为准，并要求其哈希与下载回执一致。

SQLite 中有 `source_records`、`games`、`rounds`、`decisions`、`meta`。`rounds.events` 保留原始标签、属性和事件序号；每条 `decisions.snapshot` 是独立的 zlib 压缩 JSON。检索字段独立成列并建索引，手牌使用 37 个槽位的张数向量。普通五与赤五可分开或归一化匹配。

稳定记录 ID：`<log_id>:<实际局序号，从0开始>:<XML子元素事件序号，从0开始>`。同一个局数和本场再次出现也不会覆盖记录。下一轮可从原始事件或既有快照派生特征，不必重新下载。

## 状态口径

快照取在“摸牌或鸣牌完成之后、本次切牌发生之前”。摸切按物理牌 ID 判断，不把“切出了另一张相同牌”误判成摸切。立直宣言牌尚未扣除本次立直棒，只有收到 `REACH step=2` 后才减 1000 点并增加场上立直棒。同点次位按对局开始时的座次优先顺序，不使用当前庄家或最终名次排序。

校验包括：精确账号、完整终局标记、52 张不重复配牌、摸牌物理 ID 不重复、弃牌确实在手、鸣牌消耗正确、切牌前手牌张数、立直分数转换、SQLite 唯一键、外键和完整性检查。解析器覆盖吃、碰、暗杠、大明杠、加杠、连庄、流局和同局多个和牌结果；三麻明确拒绝。

## 测试与接口

```bash
python -m unittest discover -s tests -v
```

测试包含三份真实 XML 与独立导出的天凤 JSON 对照，以及赤牌、重复张数、摸切、立直时序、杠编码、无未来信息、筛选组合、分页、只读查询和路径边界。前端以桌面和移动视口进行浏览器检查；本次测试环境限制浏览器直连 localhost，因此视觉／交互检查使用真实 API 的桥接传输，HTTP 路由另外独立测试。

只读接口：`/api/status`、`/api/search`、`/api/decision/<id>`、`/api/missing`、`/api/raw/<log_id>`。查询参数见 `luckyj.py:search`，只接受明确白名单；不提供任意 SQL。浏览器链接保存检索参数，详情使用 `#case=` 保存稳定记录 ID。

来源：天凤牌谱屋 `https://nodocchi.moe/tenhoulog/`、其 `api/listuser.php` 清单、天凤原始 XML 与 JSON 导出。鸣牌编码交叉参照 MahjongRepository/tenhou-python-bot 的公开 decoder 实现；不将第三方数据或参考代码自动套用本项目的授权。
