# LuckyJ · 牌谱索引与牌形研究室

## 重点标记更新

研究室现已重点显示跟切、立直宣言切牌、默听、退向听，并提供筛选与可重叠全集计数；新增副露状态筛选。更新后重新执行 `python -m luckyj enrich --data data`。详见 [标记、公网与副露研究设计](docs/highlights-public-and-melds.md)。公网部署仍处于方案阶段，未开启站点。

## 副露与逐手研究更新

已加入暗手+副露方括号查询、鸣牌/杠机会索引、牌河虚影与副露同色配对、听牌后的假设和牌役番、逐手押引专家HMM及人工复核入口。

```sh
python -m pip install -r requirements.txt
python -m luckyj enrich --data data
python -m luckyj serve --data data
```

研究索引版本为 `research-1.2`。役番计算需要固定的 `mahjong==1.4.0`；其他核心搜索仍可无第三方依赖运行。押引后验**未经人工标签校准**，在线与未来辅助复盘分开，不当作意图真值或准确率。见 [副露、役番和逐手推断](docs/calls-yaku-intent.md)。

## 第二阶段入口

在同一模块化主线上增加：同形/通配搜索、逐牌损存、五个时点向听、合法候选牌理、全量统计与只读MCP接口。

```sh
# 使用 main 正式数据包的 data/，不是旧 PR #2 的 SQLite
python -m luckyj enrich --data data
python -m luckyj serve --data data
# http://127.0.0.1:8000/research
```

运行无需第三方依赖；可选 `python -m luckyj native` 编译本地C加速。默认关闭数字反转、排除自己立直后的后续摸切。好形指标有明确版本及计算预算，未决不当成零；全量事实已验证，候选牌理按需缓存。

详见 [研究室使用与指标定义](docs/research.md)、[本轮验收](docs/research-verification.md) 和 [LLM比较配方](examples/follow-vs-terminal.json)。旧检索 `/` 与下述第一阶段接口仍可用。

---

## 第一阶段档案（原有接口）

第一阶段：牌谱屋来源清单 → 天凤完整 XML → SQLite 切牌前快照 → 中文网页双向检索。

精确账号为 **`ⓝLuckyJ`**。不合并普通同名账号。当前实现只处理四人麻将；来源清单不按段位、房间或东风/半庄过滤。**没有加入向听数、牌效、危险度、攻守判断或战术标签。**

## 启动

需要 Python 3.10 或更新版本。运行程序本身只使用标准库，无需安装 Python 包、Node.js、数据库服务或前端依赖。

```sh
git clone https://github.com/zdwnss1/LuckyJ.git
cd LuckyJ
python scripts/download.py --data data
python -m luckyj build --data data
python -m luckyj serve --data data --port 8000
```

打开 `http://127.0.0.1:8000`。若已取得包含 `data/luckyj.sqlite` 的完整数据包，只需最后一个命令。直接双击 HTML 不会启动后端。

下载器可重复运行：已有完整有效 XML 不重下，失败项再次尝试。默认串行请求，间隔至少 0.5 秒，遇到 HTTP 403/429 立即停止，不绕过封禁。`--delay 2` 可进一步降速，`--manifest data/manifest.json` 可复用已归档来源清单。构建为原子全量重建：先构建临时库，完成 SQLite 完整性检查后替换目标库。

## 数据覆盖，不能只看任务是否绿灯

2026-09-18 初次实测源站返回 **1,321 条记录，1,256 个不同的可下载牌谱链接，65 条没有链接**。这不意味着 1,321 场均已下载。每次下载和构建的最终结果分别见 `data/download-report.json`、`data/index-report.json`，前端顶部也显示覆盖状态。

`all_linked_logs_indexed` 表示当前清单所有带链接牌谱已入库；`all_source_records_indexed` 才表示所有来源记录都拥有牌谱并已入库。65 条无链接记录保留在报告中，不伪造牌谱，不当作零切牌样本混入查询。下载返回成功也不表示全量解析成功。范围仅限来源清单快照，不声称涵盖该账号所有历史对局。

GitHub Actions 的 **Download LuckyJ archive** 生成原始归档；**Build LuckyJ database** 从归档生成完整数据库及可运行数据包。Actions 工件有保存期限，长期备份应保存完整数据包。成功发布的数据快照在仓库 Releases 中，不把大型 SQLite 写入 Git 历史。

## 可以怎样查询

- **场况 → 切牌**：东/南/西/北场、第几局、本场数、LuckyJ 及下家/对家/上家的分数范围和次位、自风、LuckyJ 本局第几次切牌。
- **切牌 → 场况**：具体暗手牌包含或完全相同、摸入牌、切出牌、手切/摸切、本次是否宣言立直、以当前一步为结尾的连续切牌序列。
- 两组条件可以任意组合。两个入口是筛选顺序不同，不是两个彼此隔离的库。

例如：南 4 局、四位、持点 12,000–20,000；手牌包含 `233m` 且切 `2m`；摸 `3m` 切 `2m`；连续切牌 `9m1p7z`；精确切赤五筒 `0p`。

牌串使用 `m/p/s/z`：万/筒/索/字，`0m/0p/0s` 为赤五，`1z..7z` 为东南西北白发中。包含匹配会检查张数：`233m` 必须有一张二万和至少两张三万。勾选“区分赤五与普通五”时，`5p` 不匹配赤五筒；关闭后赤五合并到五。手牌条件含当时摸牌，不含已副露的牌。完整手牌匹配只约束暗手牌，不隐含副露相同。

连续切牌模式保持输入次序，匹配 LuckyJ 本局连续的出牌，以当前一步为结尾，不跨局，不跳过途中出牌。不是“曾经出现过这些牌”的集合匹配。

## 语义口径

一行记录 = LuckyJ **每一次切牌之前**的状态，加上实际切出的牌。分数可选择“切牌前”或“本局开始”。前者按事件推进扣除已成立的立直棒；宣言牌切出前尚未扣除本次立直棒。排名同分时按起家席序排序，不按当前庄家或最终名次排序。

`turn` 明确定义为 LuckyJ 本局第几次切牌，不笼统当作全桌巡目。每局使用 `round_seq` 顺序编号，不假定“场风+局数+本场”天然唯一。稳定定位键为 `(log_id, event_seq)`；网页“复制此步链接”使用此键，内部自增 ID 只用于分页与导航。

详情展示 LuckyJ 暗手牌、摸牌、四家牌河及副露、当时公开的宝牌指示牌、当时分数与局初分数。默认不展示他家暗手牌、未公开宝牌、里宝牌或未来结算。详情可跳本局上/下一次 LuckyJ 切牌，也可打开天凤原谱；天凤链接定位到整场及 LuckyJ 视角，不冒充能精确跳到当前事件。

## 存储

```text
data/
  manifest.json                    # 原始来源清单
  manifests/<sha256>.json           # 保留过往清单快照
  raw/2023/<log_id>.xml.gz          # 原始 XML 的无损 gzip
  download-report.json              # 下载校验和、失败及无链接记录
  luckyj.sqlite                    # 查询索引，可离线重建
  index-report.json                 # 覆盖与解析校验报告
```

SQLite：`games` 保存账号座位、规则、原始 SHA-256 和解析器版本；`rounds` 保存局初状态、完整逐事件流和原始结算；`decisions` 保存场况与切牌标量、34 种牌计数及赤五计数、切牌序列和 zlib 压缩的 JSON 公共快照；`metadata` 保存 schema 版本和覆盖报告。136 张实体牌 ID 保留在原谱、事件及自己手牌快照中，用于精确识别摸切和赤牌。事件流按局 zlib 压缩，避免额外膨胀。

每场解析与插入为独立事务；错误牌谱不产生半场数据，报告明确列出失败。完整数据包的原谱和 SQLite 足以支持下一轮加特征，无需再次抓源站。SQLite 查询索引是可重建产物，不能取代原谱备份。

## API 与开发

```text
GET /api/status
GET /api/coverage
GET /api/search?wind=1&hand_no=4&rank0=4
GET /api/search?hand=233m&discard=2m&red=0
GET /api/decisions/123
GET /api/resolve?log_id=...&event_seq=...
```

结果默认 30 条，最多 100 条，返回 `next_after` 用于 keyset 分页。`rank0/score0_min/score0_max` 指 LuckyJ，1/2/3 为下家/对家/上家。`basis=decision|round_start` 控制分数与次位口径。全部查询参数化并校验范围，不接受任意 SQL。静态资源只开放固定路径。

```sh
python -m unittest discover -s tests -v
node --check luckyj/static/app.js
```

运行服务仅适合本地或受信任网络；默认只监听 127.0.0.1。公开部署需要 TLS、认证（如需要）、反向代理及查询资源限制，不建议直接将标准库 HTTP 服务暴露到互联网。当前未配置公开托管域名。

## 来源

- 来源列表：`https://nodocchi.moe/api/listuser.php?name=%E2%93%9DLuckyJ`
- 原始 XML：`https://tenhou.net/0/log/?<log_id>`
- 对照导出：`https://tenhou.net/5/mjlog2json.cgi?<log_id>`

代码为本项目实现；原始牌谱及平台标识的权利仍归各自权利人。不要将保留公开数据等同于获得不受限制的再许可。
