# DotaReplayDesk × ashfury.cn/dota2 对接方案

## 结论

DotaReplayDesk 与 OpenDota 是同一份比赛解析结果的两个取得渠道，不是两种解析。系统只保留一套解析状态和一个当前有效结果。网页只读取状态、结果和公开附件地址，不参与上传，也不接触 Bearer 上传密钥。

数据边界保持为两层：

1. 基础比赛数据：用于比赛索引和历史画像。
2. 当前比赛解析结果：用于深度复盘。优先由 DotaReplayDesk 主动上传满足；没有本地结果时才使用 OpenDota 兜底。

DotaReplayDesk 产生的独立原始文件不含 `.dem`，不打 ZIP。无论最终结果来自哪个渠道，解析数据都不能进入历史含畜量模型，只属于当前比赛的证据与深度复盘数据。

## 保持兼容的现有接口

- `PUT /artifacts/{match_id}/{artifact_type}`：保持不变，继续由桌面程序调用。
- `GET /artifacts/{match_id}/{artifact_type}`：保持不变，继续提供单文件读取。
- `GET /match/{match_id}`：保持不变，不在旧响应中强行混入附件信息。
- `/page/recent` 与 `/page/match/{match_id}`：迁移期间保留，最终可重定向到新工作台。

## 新增附件清单接口

`GET /artifacts/{match_id}`

建议响应：

```json
{
  "match_id": 9000265617,
  "source": "dota_replay_desk",
  "attachment_status": "none",
  "revision": "2026-09-17T14:30:00+08:00",
  "expected_types": [
    "replay-events",
    "combat-log",
    "combat-log-ndjson",
    "manifest"
  ],
  "available_count": 0,
  "artifacts": [],
  "privacy": {
    "public_downloads": true,
    "may_contain_player_identifiers": true,
    "may_contain_game_chat": true
  }
}
```

文件存在时，`artifacts` 中每项建议包含：

```json
{
  "type": "combat-log",
  "filename": "combat-log.txt",
  "label": "可读战斗日志",
  "content_type": "text/plain; charset=utf-8",
  "size_bytes": 1824431,
  "sha256": "...",
  "uploaded_at": "2026-09-17T14:28:17+08:00",
  "url": "/dota2/api/artifacts/9000265617/combat-log"
}
```

### 附件状态定义

- `none`：没有 DotaReplayDesk 附件。
- `partial`：已有一个或多个附件，但没有最终 manifest，或 manifest 声明的文件尚未全部存在。
- `complete`：manifest 已上传，并且它声明的附件均已存在；此时统一解析状态变为 `parsed`，来源记为 `dota_replay_desk`。

建议桌面程序最后上传 `manifest`，把它作为本地结果上传完成的提交标记。四种附件仍然独立上传和下载，不需要 ZIP。主动上传完成后，服务端立即更新统一解析状态，不需要等待定时任务。

## 解析状态更新策略

- 新比赛发现后先记录统一状态 `unparsed`，并保留 1 小时本地主动上传窗口。
- DotaReplayDesk：事件驱动。收到合法 `PUT /artifacts/...` 后立即更新附件清单；满足 manifest 声明后，将统一状态改为 `parsed`、来源记为 `dota_replay_desk`，并设置 `skip_opendota_parse=true`。
- 一小时本地优先窗口结束时，兜底任务必须先读取统一状态。若已经 `parsed`，直接跳过，不查询、不请求 OpenDota Parse。
- 只有仍未解析的比赛才检查 OpenDota：如果 OpenDota 已解析则缓存结果；如果未解析且从未提交过请求，则提交一次 Parse 请求并记为 `parsing`。
- Parse 请求是一次性动作。请求提交后，每 10 分钟同步一次 OpenDota 结果状态，直至变为 `parsed` 或明确失败；同步过程绝不重复提交 Parse 请求。
- 如果 OpenDota 请求已经提交后才收到本地结果，以本地结果满足统一状态，并停止后续 OpenDota 状态轮询。已经发出的请求无法撤回，但不会再产生第二份页面状态。

页面只显示一套状态：

- `未解析`
- `等待解析`（仍在一小时本地上传窗口）
- `解析中`（已提交 OpenDota Parse）
- `已解析`
- `解析失败`

结果来源只在“数据/API”详情中作为说明显示为 `DotaReplayDesk` 或 `OpenDota`，不得在一级页面制造两个解析概念。

## 新比赛工作台聚合接口

新前端不应自行拼接很多旧接口。建议增加：

`GET /v1/matches/{match_id}/workspace`

它只聚合状态和导航所需的轻量数据：

```json
{
  "match": {},
  "historical_profile": {
    "status": "missing",
    "url": "/dota2/api/v1/matches/9000265617/historical-profile"
  },
  "parse": {
    "status": "parsed",
    "source": "opendota",
    "requested_at": "2026-09-17T14:00:00+08:00",
    "completed_at": "2026-09-17T15:00:00+08:00",
    "result_url": "/dota2/api/match/9000265617",
    "artifact_inventory_url": "/dota2/api/artifacts/9000265617"
  },
  "review": {
    "status": "published",
    "url": "/dota2/matches/9000265617/review"
  }
}
```

大型 NDJSON、TXT 和 OpenDota 完整 JSON 不应内嵌在聚合响应中。

## 页面呈现

- 一级 `/dota2`：最近比赛表格只显示一列 `解析状态`，例如 `已解析`、`等待解析` 或 `解析中`。
- 单场比赛工作台：固定为 `历史成分`、`深度复盘`、`数据/API` 三部分。
- `数据/API`：展示基础数据与一份统一解析结果。结果详情可以说明本次来源，但不创建第二套解析状态。
- 没有 DotaReplayDesk 文件时不生成附件链接；这不影响由 OpenDota 获得统一结果。
- 有文件时逐项显示，不提供不存在的链接，不提供 ZIP，也不出现 `.dem`。
- 原始附件区默认收起，并在展开或下载前显示“可能包含玩家标识或游戏内聊天”的提醒。

## 服务端记录

上传成功后建议保存 `match_id`、类型、文件名、MIME、大小、SHA-256 和上传时间，避免清单接口每次重新计算哈希。可写入统一数据库的 `match_artifacts` 表；迁移前也可以使用每场比赛一个服务端元数据索引文件。

公开下载继续添加 `X-Content-Type-Options: nosniff`，并建议增加 `Content-Disposition: attachment` 与 `X-Robots-Tag: noindex, nofollow`。比赛归属校验不等于下载者身份验证，因此页面必须明确说明这些链接当前是公开的。
