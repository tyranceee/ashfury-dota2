# ashfury.cn Dota「历史成分 / 含畜量分析」第一阶段数据边界报告

> 数据源决策更新（2026-09-17）：V0.1 改为 Steam Web API 检测 Dota 在线状态、OpenDota 基础数据构建历史画像；在线时每 10 秒检测最新 Match ID，发现新比赛后允许提交一次 OpenDota Parse，但 Parse 结果只供“深度复盘”，禁止进入“历史含畜量”评分。真实 assigned 1–5 暂不启用，不能用 OpenDota `lane_role` 冒充。

审计日期：2026-09-17（Asia/Shanghai）  
状态：**可以继续做原型，但正式评分实现前必须先通过 Ranked Roles GC 字段 POC。**

## 1. 结论摘要

1. 当前 ashfury.cn 的 Dota 数据服务只围绕账号 `212121467` 维护最近 100 场，数据源是 OpenDota；线上没有数据库，使用 `matches_index.json` 和逐场 JSON 文件缓存。
2. 当前 watcher 每 300 秒刷新一次，并对最近 20 场主动提交 OpenDota Replay Parse。这个链路不满足新模块的“零 Replay / 零 Parse”边界，历史模块必须单独建设基础数据入口，不能复用自动 Parse 流程。
3. **基础终局数据不需要 Replay Parse。** 实测 10 场 `version=null / has_parsed=false` 的比赛，10/10 场的 10 名玩家均有 K/D/A、LH、denies、GPM、XPM、level、net worth、hero/tower damage、healing 等终局字段。
4. **OpenDota 公共 JSON 当前不提供真实 assigned 1–5。** 未解析样本中 `lane_role` 和 `lane_selection_flags` 均为 0 场可用；`lane_role` 不是可接受的无 Replay 真值。
5. **Valve GC 比赛详情协议确实定义了 `CMsgDOTAMatch.Player.lane_selection_flags`，并有五个明确枚举。** 映射为：`1=Carry`、`4=Mid`、`2=Offlane`、`8=Soft Support`、`16=Hard Support`。这是目前最有希望的真实位置来源，而且不需要 Replay。
6. OpenDota 自己也是通过 GC `CMsgGCMatchDetailsRequest` 获取比赛详情，但其当前 `GcdataFetcher` 只保留 account、slot、party 等字段，主动丢弃了 `lane_selection_flags`，所以 ashfury 现有 OpenDota 响应看不到该字段。
7. **“任意玩家稳定拿满赛前 30 场”不能保证。** 以 Match `9001763544` 的另外 9 人为实测样本：6 人可取到至少 30 场赛前天梯，3 人返回 0 场。公开覆盖率为 6/9；私密比赛历史没有可靠、合规的绕过方案。
8. 严格防泄漏可以做到：样本必须同时满足 `history_match_id < target_match_id`、`history_start_time + duration <= target_start_time`，再取时间最近的 30 场。画像必须保存为以 target match 为 cutoff 的不可变快照，未来新比赛不能回填旧画像。
9. 10 秒级持续轮询 OpenDota 不合适：无 key 当前限制实测为 60 次/分钟、3000 次/天；全天每 10 秒一次会产生 8640 次/天。推荐由 Steam/Dota 在线状态或本机轻量心跳触发“在线 10 秒、离线停用高频轮询”，比赛结束后才执行一次增量画像更新。

## 2. 当前 ashfury.cn 真实链路

线上只读审计得到：

```text
Nginx /dota2/api/
  -> 127.0.0.1:3001 FastAPI REST
     -> /opt/dota2-mcp/rest_server.py

/opt/dota2-mcp/watcher.py
  -> 每 300 秒 GET OpenDota /players/212121467/matches?limit=100
  -> 维护 matches_index.json
  -> 检查最新 20 场
  -> 未解析时 POST OpenDota /request/{match_id}
  -> 解析完成后保存 data/{match_id}.json

127.0.0.1:3000
  -> MCP server.py
```

当前存储：

- `matches_index.json`：最近 100 场摘要与 Parse 状态；
- `data/<match_id>.json`：完整 OpenDota JSON；
- 没有 players、matches、match_players 等关系表；
- 没有任意玩家历史画像缓存；
- 没有真实 assigned role 保存字段；
- 当前 REST `/match/{id}` 只允许用户最近 100 场。

因此新模块需要独立 ingestion/cache 层；现有 Replay 深度复盘缓存可以继续保留，但两者不能混用数据完整性判定。

## 3. 零 Parse 可得字段

### 3.1 玩家历史列表：OpenDota `/players/{account_id}/matches`

实测直接返回：

| 字段 | 可用性 | 说明 |
|---|---:|---|
| match_id | 稳定 | 可用于去重与次级 cutoff |
| start_time | 稳定 | 主 cutoff 字段 |
| duration | 稳定 | 与 start_time 组合成 end_time |
| lobby_type | 稳定 | `7` 为 Ranked；Ranked Classic 与 Ranked Roles 都在其中，不能仅凭它确认 Roles |
| game_mode | 稳定 | 当前常见 All Pick 为 `22` |
| hero_id / hero_variant | 稳定 | 英雄与 facet |
| player_slot | 稳定 | 阵营与槽位 |
| radiant_win | 稳定 | 可计算个人胜负 |
| kills / deaths / assists | 稳定 | 摘要层可用 |
| leaver_status | 稳定 | 应排除 abandon/异常局或单独标记 |
| party_size | 常见 | 可能为空 |
| average_rank | 常见 | 来源/时间语义不够严格，V0.1 不用于赛前评分 |
| version | 常见 | `null` 表示无 Replay Parse，不影响基础终局数据 |
| LH/GPM/XPM/伤害/治疗/终局物品 | 条件可得 | 默认响应不一定携带；实测用重复 `project` 参数可直接投影基础列，不必请求单场详情。依赖 Parse 才生成的列仍必须排除 |

补充实测：`historical_10_smoke_test` 只调用玩家历史列表、未请求任何单场详情或 Parse；10/10 场取得 K/D/A、GPM、XPM、LH、denies、hero/tower damage、healing、level、party size、leaver status 与终局物品，`net_worth` 为 0/10。`lane`、`lane_role` 即使返回也不作为真实 assigned role，更不能成为历史模块的必需字段。

### 3.2 单场基础详情：OpenDota `/matches/{match_id}` 或 GC Match Details

在 `version=null` 的 10 场样本中，下列核心终局数据均覆盖 10/10 名玩家：

| 类别 | 字段 | 无 Parse 可得 | 备注 |
|---|---|---:|---|
| 身份 | account_id | 条件可得 | OpenDota 未取得 GC 数据时可能匿名；GC 详情可补全 |
| 槽位 | player_slot、team_number、team_slot | 是 | 不等于 1–5 号位 |
| 英雄 | hero_id、hero_variant | 是 | hero_variant 为 facet |
| 装备 | item_0…、backpack、neutral item | 是 | 仅终局装备，不是购买时间线 |
| 战绩 | kills、deaths、assists、leaver_status | 是 | |
| 经济 | last_hits、denies、gold_per_min、net_worth、gold、gold_spent | 是 | 终局统计 |
| 经验 | xp_per_min、level | 是 | |
| 贡献 | hero_damage、tower_damage、hero_healing | 是 | 可与资源做转换效率 |
| 技能 | ability upgrades | 条件可得 | 是升级结果/时间字段，不是完整战斗日志 |
| 组队 | party_id、party_size | 条件可得 | |
| 比赛 | start_time、duration、radiant_win、双方比分、lobby_type、game_mode | 是 | |
| 位置 | GC lane_selection_flags | 协议有，待 POC | 真实 Ranked Roles 候选真值 |
| OpenDota lane/lane_role | 未解析时无 | 否 | 不能拿来冒充真实 assigned role |

### 3.3 明确禁止进入历史模块的字段

以下字段缺失时不得 Parse，也不得以 Deep Review 缓存补齐：

- teamfights；
- radiant_gold_adv / radiant_xp_adv；
- gold_t / xp_t / lh_t / hero_damage_t；
- lane_pos、死亡坐标、逐分钟位置；
- kills_log、damage_targets、damage_inflictor；
- purchase_log、purchase_time、item_uses；
- objectives、战斗日志、Replay 时间轴。

## 4. P0：Ranked Roles 真实位置调查

### 4.1 Steam WebAPI

结论：**目前不能作为已验证方案。**

- Dota `GetMatchDetails` 需要 Web API key；无 key 实测为 HTTP 403。
- OpenDota 当前源码已经把直接 `GetMatchDetails` 标注为 broken，并改用其他基础数据/GC 流程。
- 旧的 WebAPI JSON 样本不含 `lane_selection_flags`；没有证据可证明当前公开 WebAPI JSON 会稳定暴露该字段。
- 所以不能在模型里假设 Steam WebAPI 能直接给 1–5。

### 4.2 Dota GC 协议

结论：**协议层面可行，是首选路线；生产可用性仍需一次实局 POC。**

证据：

- `CMsgGCMatchDetailsRequest(match_id)` 返回 `CMsgGCMatchDetailsResponse.match`；
- `CMsgDOTAMatch.Player` 包含 `lane_selection_flags = 75`；
- 枚举定义：Safelane=1、Offlane=2、Midlane=4、Support=8、Hard Support=16；
- 同一 GC 详情同时带 account_id、英雄、装备、KDA、LH、GPM/XPM、伤害、治疗、等级、净资产等终局数据；
- 此请求是比赛详情 GC 消息，不需要下载或解析 Replay。

1–5 映射：

| GC flag | Valve lane selection | 产品位置 |
|---:|---|---|
| 1 | SAFELANE | 1 Carry |
| 4 | MIDLANE | 2 Mid |
| 2 | OFFLANE | 3 Offlane |
| 8 | SUPPORT | 4 Soft Support |
| 16 | HARDSUPPORT | 5 Hard Support |

注意：字段名是 flags，排队前也可表示多选意愿；只有验证赛后 `CMsgDOTAMatch.Player` 在 Ranked Roles 中确实为每人一个 one-hot 值，才能称为“真实 assigned role”。

### 4.3 当前 ashfury/OpenDota 链路

结论：**当前拿不到。**

OpenDota 的 GC retriever 会收到完整 GC match，但当前 `GcdataFetcher` 转存时只保留：

- account_id；
- player_slot；
- party_id；
- permanent_buffs；
- party_size。

它没有把 `lane_selection_flags` 写入最终 GC data，因此 ashfury 现有 JSON 中该字段缺失。OpenDota 的 `lane_role` 则来自解析/推断链路，不符合本项目要求。

### 4.4 必须执行的 POC 验收

用专用 Steam/GC 服务账号，对至少 20 场已知 Ranked Roles 比赛发送 GC Match Details 请求：

1. 不下载 Replay，不调用 OpenDota Parse；
2. 保存每名玩家的原始 `lane_selection_flags`；
3. 每队应各出现且只出现一次 `1、4、2、8、16`；
4. 用户本人旗标必须与 Dota 客户端给出的 assigned role 一致；
5. 对 Ranked Classic 样本应记录为 `0/unknown`，不能强制推成 1–5；
6. 连续 20 场全部满足后，将 `role_source=gc_assigned` 标为 exact；否则暂停位置归一化模型。

在 POC 通过前，页面只能显示“位置待验证”，不能使用 GPM/LH 猜位。

## 5. 赛前最近 30 场可行性与隐私边界

### 可行路径

1. 获取目标 Match X 的 `start_time`、`match_id` 和 10 名玩家 account_id；
2. 对另外 9 人读取公开 match history，分页拉到足够多；
3. 仅保留 `lobby_type=7`；
4. 严格过滤：

```text
history.match_id < X.match_id
AND history.start_time + history.duration <= X.start_time
AND history.match_id != X.match_id
```

5. 按 `(start_time DESC, match_id DESC)` 排序，取前 30；
6. 每个唯一 match_id 只取一次 GC/basic details，然后从 10 人中抽出目标玩家行。

### 实测覆盖

以 Match `9001763544` 的 9 名其他玩家为样本：

- 6 人：可取得至少 30 场赛前 Ranked；
- 3 人：历史接口返回 0 场；
- 当前单场样本覆盖率：66.7%。

这说明产品必须支持：

- `PUBLIC_COMPLETE`：30 场；
- `PUBLIC_PARTIAL`：1–29 场；
- `PRIVATE_OR_UNAVAILABLE`：0 场；
- 私密玩家不输出伪精确 S/L/F，只显示“历史不可见”；
- 深度复盘读取画像时必须同时读取 sample size、role coverage 和 confidence。

## 6. 缓存与数据库 V0.1

服务器目前是单用户、低并发，建议先用 SQLite WAL；未来多人化再迁 PostgreSQL。不要继续用一个大 JSON 索引承担画像计算。

### 核心表

**players**

- account_id PK
- history_visibility
- first_seen_at / last_seen_at
- last_history_sync_at
- newest_known_match_id

**matches**

- match_id PK
- start_time / duration / end_time
- lobby_type / game_mode / patch
- radiant_win / team scores
- basic_source、basic_fetched_at、basic_complete
- raw_payload_hash；原始 JSON 可放对象文件，不重复存 10 份

**match_players**

- PK `(match_id, player_slot)`
- account_id、team_number、team_slot、hero_id、hero_variant
- role_flags、assigned_position、role_source、role_validated
- K/D/A、LH、denies、GPM、XPM、level、net_worth
- hero_damage、tower_damage、hero_healing
- final_items_json、leaver_status、party_id

**player_match_features**

- PK `(account_id, match_id, feature_version)`
- duration-normalized metrics
- team kill participation
- role-benchmark percentiles/z-scores
- conversion、survival、contribution、risk 子分

**hero_role_benchmarks**

- benchmark_version、patch_group、assigned_position、metric
- sample_n、p10/p25/p50/p75/p90、median、MAD
- 小样本时回退到 position + broader patch，不回退到猜位

**player_history_profiles**

- PK `(account_id, cutoff_match_id, model_version)`
- cutoff_start_time
- sample_size、role_coverage、visibility、confidence
- strength_S、liability_L、form_F
- common_roles_json、hero_pool_json、risk_tags_json
- computed_at

**profile_sample_matches**

- profile key + match_id + recency_index + decay_weight
- 用于逐条审计样本，证明 Match X 没有混入

**player_score_cache**

- 指向某玩家最新可复用画像；
- 只能作为加速层，历史深度复盘必须按 cutoff 读不可变 snapshot，不能读“今天的最新画像”。

**monitor_state / ingestion_jobs**

- Steam/Dota presence、last transition、active polling lease；
- 新比赛发现、history refresh、GC details fetch、profile compute 的幂等任务状态。

### 增量更新

```text
发现新 Match X
  -> upsert X 与 10 名玩家
  -> 对 9 人从 newest_known_match_id 向前/向后补增量历史
  -> 已存在的 match_id 不再请求
  -> 仅拉新增或缺字段的 GC/basic details
  -> 为 X cutoff 选出各自最近 30 场
  -> 生成不可变 profile snapshot
```

冷启动最多约 9×30 个玩家样本，但按 match_id 去重；以后每出现一场新比赛，通常每个玩家只新增 0–1 场，成本会迅速下降。

## 7. V0.1 历史画像模型草案

### 前置质量门

- POC 未通过或某局 role_flags 非 one-hot：该局 `role=unknown`，不做 1–5 号位归一化；
- 可用样本 `<10`：不输出数值 S/L/F；
- 10–19 场：低置信度；20–29 场：中置信度；30 场且 role coverage ≥80%：高置信度；
- abandon、极短异常局、非 Ranked 单独排除或降权；
- OpenDota 响应时的 current rank/personaname/computed_mmr 不进入历史评分，避免把未来状态泄漏到旧 Match X。

### 时间权重

默认按比赛新旧序号衰减：

```text
w(i) = 2 ^ (-(i-1) / H)
```

- `i=1` 为离 Match X 最近的一场；
- 默认半衰期 `H=10`，做成配置；
- 同时保存 30 场加权总体、最近 10 场、最近 5 场三个窗口；
- F 只表达近期相对自身 30 场基线的变化，不与 S 混成同一个分。

### 同位置基础特征

所有指标先按真实 assigned position 做 patch/position 基准的 robust percentile 或 median/MAD 标准化：

- 结果：贝叶斯收缩后的 win contribution；
- 生存：deaths/10min、死亡稳定性；
- 参战：`(K+A)/本队击杀`；
- 资源：GPM、XPM、LH/10min、net_worth/min；
- 输出：hero_damage/min、tower_damage/min、healing/min；
- 转换：hero/tower/healing contribution 相对 net worth 或 GPM；
- 稳定：核心子分的离散程度和尾部差局比例；
- 英雄熟练：英雄场次 + 同英雄同位置表现，经贝叶斯收缩，避免 1–2 场爆种。

### 三个分数

**历史实力 S（0–100）**

- 25% 结果能力（收缩胜率，不把胜率当全部）；
- 25% 战斗/参战贡献；
- 20% 资源获取与贡献转换；
- 15% 生存；
- 10% 推进/治疗等职责贡献；
- 5% 稳定性。

**历史含畜量 L（0–100，越高风险越大）**

- 35% 同位置死亡风险；
- 30% 吃资源低转换；
- 20% 波动与灾难局尾部；
- 15% 长期低贡献/低结果的联合证据。

L 不直接使用“低胜率=畜”，也不以单局 KDA 判定。

**近期状态 F（建议显示 -100…+100）**

- 60% 最近 5 场相对 30 场基线的变化；
- 40% 最近 10 场相对 30 场基线的变化；
- 同时观察 composite、死亡风险、转换效率和稳定性；
- 负向显著才标“近期红温”，避免一两场噪声。

### 标签初稿

- **高死亡型**：同位置死亡风险长期处于高分位，且不是仅最近一场造成；
- **吃资源低产出**：资源分位不低，但输出/推进/治疗转换显著偏低；
- **近期红温**：最近 5/10 场 composite 明显低于个人 30 场基线，且死亡或转换至少一项同步恶化；
- **稳定型辅助**：4/5 号位占比高，贡献稳定、低尾部风险；
- **高能高危**：贡献和死亡风险同时高；
- **绝活哥**：30 场中同英雄样本足够，且同英雄同位置表现经收缩后仍显著高于基准；
- **数据不足**：公开样本或位置真值不足，替代武断标签。

阈值要在 benchmark 样本形成后再冻结；当前只确定结构，不伪造具体百分位门槛。

## 8. 10 秒级刷新与在线状态方案

### 不建议

直接每 10 秒请求 OpenDota：

- 全天 8640 次/天，超过当前无 key 3000 次/天；
- 一局 45 分钟会浪费约 270 次历史查询；
- 比赛进行中，历史画像不会因为这一局尚未结束而变化；
- OpenDota 新比赛入库也可能有延迟，10 秒轮询不等于 10 秒可见。

### 推荐状态机

```text
OFFLINE
  -> 仅保留低频 Steam presence（60–120 秒），或由本机心跳完全唤醒

DOTA_ONLINE / IN_MENU
  -> 10 秒检查 presence/rich presence
  -> 不重算 9 人画像

IN_MATCH
  -> 10 秒保持状态；记录进入比赛时间

MATCH_ENDED
  -> 每 10 秒查最新 match ID，最多 3–5 分钟并退避
  -> 一旦发现新 Match X，触发一次增量 ingestion/profile
  -> 成功后停止高频轮询
```

在线检测优先级：

1. **本机轻量心跳/现有桌面程序**：检测 Steam/Dota 进程或 Dota 本地状态后通知服务器；最省外部 API，离线时服务器可完全停止高频轮询。
2. **Steam presence**：通过 Steam WebAPI `GetPlayerSummaries` 或专用 Steam 好友/GC 会话读取 `gameid=570`/rich presence；需要 API key、公开游戏详情或专用账号，并必须实测隐私设置下的可见性。
3. **OpenDota 兜底**：离线 5 分钟级、检测到 Dota 在线后 10 秒级，但只用于发现新 match，且受 3000/天限制。

建议采用 1+2，OpenDota 只做兜底。页面的“数据更新时间”应区分：在线状态刷新时间、最新比赛发现时间、画像计算完成时间。

## 9. 进入正式实现前的 Gate

必须依次确认：

1. GC Ranked Roles POC 通过，证明赛后 `lane_selection_flags` 为真实 one-hot assigned role；
2. 确认专用 GC 账号/IP 的实际限流，避免按非官方上限设计；
3. 选定在线唤醒方式：本机心跳或 Steam presence；
4. 接受“私密玩家只显示数据不足，不强行评分”；
5. 确认 V0.1 的 S/L/F 结构，再冻结具体权重和标签阈值；
6. 之后才开始数据库、ingestion 和页面实现。

## 10. 主要证据链接

- [Valve Steamworks：ISteamUser / GetPlayerSummaries](https://partner.steamgames.com/doc/webapi/ISteamUser#GetPlayerSummaries)
- [Dota GC：CMsgDOTAMatch.Player.lane_selection_flags](https://github.com/SteamTracking/GameTracking-Dota2/blob/f61235b7975ff3be0131e57076191b61410c15ec/Protobufs/dota_gcmessages_common.proto#L917)
- [Dota GC：五种 Lane Selection Flags](https://github.com/SteamTracking/GameTracking-Dota2/blob/f61235b7975ff3be0131e57076191b61410c15ec/Protobufs/dota_gcmessages_common_match_management.proto#L5)
- [Dota GC：Match Details 请求/响应](https://github.com/SteamTracking/GameTracking-Dota2/blob/f61235b7975ff3be0131e57076191b61410c15ec/Protobufs/dota_gcmessages_client.proto#L325)
- [OpenDota：GC retriever 实现](https://github.com/odota/core/blob/60b22096ffd0be5f11285af3b14473033956c634/svc/retriever.ts#L50)
- [OpenDota：GcdataFetcher 转存字段](https://github.com/odota/core/blob/60b22096ffd0be5f11285af3b14473033956c634/svc/fetcher/GcdataFetcher.ts#L71)
- [OpenDota：当前 GetMatchDetails 被标记为 broken](https://github.com/odota/core/blob/60b22096ffd0be5f11285af3b14473033956c634/svc/fetcher/ApiFetcher.ts#L21)
- [OpenDota：玩家比赛历史 API](https://docs.opendota.com/#tag/players/GET/players/{account_id}/matches)
- [OpenDota：当前免费额度与分钟限制配置](https://github.com/odota/core/blob/60b22096ffd0be5f11285af3b14473033956c634/config.ts#L59)
