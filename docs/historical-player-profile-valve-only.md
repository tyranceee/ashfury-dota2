# 历史成分模块：Valve-only 数据方案

审计日期：2026-09-17（Asia/Shanghai）

## 决策

历史成分模块可以做到**运行时完全不依赖 OpenDota**，但必须区分两种含义：

- 只允许 Valve 官方公开、正式文档化的 Steam WebAPI：**不能完整支持**，主要缺口是真实 Ranked Roles 位置和接口稳定性。
- 允许直接连接 Valve 的 Steam / Dota Game Coordinator（GC）：**字段层面可以支持核心功能**，但 GC 不是 Valve 承诺稳定的第三方公共 API，需要专用 Steam 服务账号、限速、重连和协议升级维护。

推荐采用第二种：运行时所有比赛数据直接来自 Valve GC，OpenDota 不进入生产数据链路。

## Valve-only 闭环

### 1. 任意公开玩家的比赛历史

GC 消息：

```text
CMsgDOTAGetPlayerMatchHistory
  account_id
  start_at_match_id
  matches_requested
  hero_id
```

响应直接包含：

```text
match_id, start_time, hero_id, winner, game_mode,
rank_change, previous_rank, lobby_type, solo_rank,
abandon, duration, selected_facet
```

可以一次请求 30 场或分页请求更多，再在服务端做严格 cutoff。

隐私边界是 Valve 自己定义的 `allow_3rd_party_match_history`。玩家关闭第三方比赛历史时，Valve GC 不保证返回其历史；这种玩家只能标记为 `PRIVATE_OR_UNAVAILABLE`，不能绕过。

### 2. 单场 10 人终局详情

GC 消息：

```text
CMsgGCMatchDetailsRequest(match_id)
  -> CMsgGCMatchDetailsResponse.match
  -> CMsgDOTAMatch
```

无需 Replay 即可取得：

- account_id、player_slot、team_number、team_slot；
- hero_id、selected_facet；
- 终局物品；
- kills、deaths、assists、leaver_status；
- last_hits、denies、GPM、XPM、gold、gold_spent；
- level、net_worth；
- hero_damage、tower_damage、hero_healing；
- party_id；
- lane_selection_flags；
- 条件性扩展字段：disable_duration、seconds_dead、gold_lost_to_death、scaled damage/healing、bounty runes、outposts 等。

最后一组字段虽然存在于协议，是否每场稳定填充仍需真实样本覆盖测试，V0.1 不应预设其必有。

### 3. Ranked Roles 真实 1–5

`CMsgDOTAMatch.Player.lane_selection_flags` 的枚举为：

| 原始值 | Valve 枚举 | 产品位置 |
|---:|---|---|
| 1 | SAFELANE | 1 Carry |
| 4 | MIDLANE | 2 Mid |
| 2 | OFFLANE | 3 Offlane |
| 8 | SUPPORT | 4 Soft Support |
| 16 | HARDSUPPORT | 5 Hard Support |

这是官方后端协议里的字段，不是根据 GPM/LH 推断的位置。

正式使用前仍需 POC：至少验证 20 场已知 Ranked Roles，每队应各出现一次 `1、4、2、8、16`，并与用户本人客户端显示的位置一致。Ranked Classic 或异常返回值保存为 unknown，不推断。

### 4. 严格赛前 cutoff

历史样本必须同时满足：

```text
history.match_id < target.match_id
AND history.start_time + history.duration <= target.start_time
AND history.match_id != target.match_id
AND history.lobby_type == RANKED
```

然后按 `(start_time DESC, match_id DESC)` 取 30 场。画像保存为 `(account_id, cutoff_match_id, model_version)` 不可变快照。

## 在线与比赛结束监测

Valve-only 有两种方式：

1. 专用 Steam 服务账号与用户互为好友，保持 Steam 长连接，接收 persona / rich presence 变化；检测到 Dota 2（AppID 570）后进入 active 状态。该方式以事件推送为主，不需要离线时每 10 秒轮询。
2. Steam WebAPI `GetPlayerSummaries` 读取公开在线/游戏状态；需要 Web API key，且受用户游戏详情隐私设置影响。

已确认采用并完成代码验证的状态机：

```text
每 10 分钟
  -> GetPlayerSummaries 检查目标账号是否正在运行 AppID 570

非 Dota / 离线
  -> 不查询比赛结果
  -> 关闭专用 GC 会话

Dota 在线
  -> 每 10 秒只查询本人最新已完成 Match ID
  -> Match ID 未变化时不拉详情
  -> 发现新 Match ID 后只拉一次 Valve GC 比赛详情并落盘
  -> 下一次 10 分钟在线检查发现离线后，停止 10 秒循环
```

实现位于 `valve-monitor/`。监控状态持久化，服务重启不会重复处理；Valve
详情暂未就绪时不会把比赛标为完成，下一个 10 秒周期会重试。首个 Match ID
仅作为基线，避免部署当天误处理旧比赛。

如果 Steam 隐私导致 presence 不可见，可由用户电脑上的轻量程序检测 Dota 进程并向服务器发送 online/offline 心跳；比赛数据本身仍全部来自 Valve GC。

## 工程与账号约束

- 使用独立 Steam 服务账号，不在服务器保存用户主账号密码；
- Steam Guard 登录后保存受保护的 refresh token；
- 连接 Dota GC 前以 AppID 570 建立游戏会话并发送 GC hello；
- 请求必须限速、指数退避、幂等缓存；Valve 未公开 GC 请求额度或 SLA；
- GC 协议更新时自动检测 protobuf/schema 版本，未知字段不导致任务失败；
- 任何 account history 私密/空响应都视为数据不可见，不尝试规避；
- 不调用 Replay、Parse、OpenDota、STRATZ 或其他第三方比赛数据源。

## 最终判断

| 能力 | Valve WebAPI only | Valve GC | 结论 |
|---|---:|---:|---|
| 本场 10 人账号 | 不稳定 | 支持 | 用 GC |
| 任意玩家赛前历史 | 受隐私限制 | 支持公开历史 | 用 GC history |
| KDA/LH/GPM/XPM/伤害 | 部分接口可得 | 支持 | 用 GC details |
| 终局装备 | 支持 | 支持 | 用 GC details |
| 真实 Ranked Roles 1–5 | 未验证/不可依赖 | 协议字段支持 | POC 后启用 |
| 零 Replay / 零 Parse | 支持 | 支持 | 满足 |
| 私密历史绕过 | 不支持 | 不支持 | 必须显示数据不足 |
| 官方稳定 SLA | 无充分保证 | 无 | 需要自维护与降级 |

结论：**核心模型可以完全建立在 Valve GC 数据上，不依赖 OpenDota；但不能保证私密玩家有 30 场，也不能把 GC 描述成 Valve 正式承诺稳定的公共 API。**

## 证据

- [Valve GC：玩家比赛历史请求与响应](https://github.com/SteamTracking/GameTracking-Dota2/blob/f61235b7975ff3be0131e57076191b61410c15ec/Protobufs/dota_gcmessages_client.proto#L701)
- [Valve GC：比赛详情请求与响应](https://github.com/SteamTracking/GameTracking-Dota2/blob/f61235b7975ff3be0131e57076191b61410c15ec/Protobufs/dota_gcmessages_client.proto#L325)
- [Valve GC：CMsgDOTAMatch 玩家终局字段](https://github.com/SteamTracking/GameTracking-Dota2/blob/f61235b7975ff3be0131e57076191b61410c15ec/Protobufs/dota_gcmessages_common.proto#L917)
- [Valve GC：五种 Ranked Roles flags](https://github.com/SteamTracking/GameTracking-Dota2/blob/f61235b7975ff3be0131e57076191b61410c15ec/Protobufs/dota_gcmessages_common_match_management.proto#L5)
- [Valve GC：第三方比赛历史开关](https://github.com/SteamTracking/GameTracking-Dota2/blob/f61235b7975ff3be0131e57076191b61410c15ec/Protobufs/dota_gcmessages_client.proto#L349)
- [Valve Steamworks：GetPlayerSummaries](https://partner.steamgames.com/doc/webapi/ISteamUser#GetPlayerSummaries)
