# DotaReplayDesk 上传可靠性更新任务

请先检查现有 DotaReplayDesk 项目结构、解析流程、配置保存方式和上传代码，在现有架构上做最小改动，不要重写解析器。

## 一、目标

DotaReplayDesk 负责在 Windows 本地解析 Dota 2 Replay，并把四个独立文件上传到 ashfury.cn。

本次只完善上传可靠性、断点续传、完成确认和安全存储。不要在桌面程序中实现历史含畜量、OpenDota Parse、Dota 在线监测、ChatGPT 启动或深度复盘任务创建。

本地解析是可选功能。用户没有执行本地解析时，程序不需要向服务器发送“跳过”或“失败”信号。

## 二、服务器接口

接口根地址：

```text
https://ashfury.cn/dota2/api
```

查询某场比赛已经上传的附件：

```http
GET /artifacts/{match_id}
```

上传单个附件：

```http
PUT /artifacts/{match_id}/{artifact_type}
Authorization: Bearer <本地安全保存的上传密钥>
Content-Type: <对应文件类型>

<文件原始字节>
```

上传接口只允许下列四种 `artifact_type`：

| artifact_type | 本地文件 | 建议 Content-Type |
|---|---|---|
| `replay-events` | `replay-events.ndjson` | `application/x-ndjson` |
| `combat-log` | `combat-log.txt` | `text/plain; charset=utf-8` |
| `combat-log-ndjson` | `combat-log.ndjson` | `application/x-ndjson` |
| `manifest` | `manifest.json` | `application/json` |

不要上传 `.dem`，不要生成或上传 ZIP。

## 三、必须实现的流程

1. 用户选择或完成一场本地解析后，先调用 `GET /artifacts/{match_id}`。
2. 读取响应中的 `artifacts[].type`，已经存在的类型默认跳过，实现断点续传。
3. 按以下顺序上传：
   - `replay-events`
   - `combat-log`
   - `combat-log-ndjson`
   - `manifest`（最后上传）
4. 每个文件独立上传、独立显示进度、独立重试。
5. 四个文件上传完成后，再调用一次 `GET /artifacts/{match_id}`。
6. 只有同时满足以下条件，桌面程序才显示“服务器已确认解析完成”：
   - `available_count == 4`
   - `attachment_status == "complete"`
7. 最后一个上传响应正常情况下还应返回：

```json
{
  "parse_status": "parsed"
}
```

如果只上传了一部分，应显示“已上传 N/4，可继续上传”，不能显示完整成功。

## 四、上传响应

每次成功的 PUT 会返回类似内容：

```json
{
  "match_id": 9003201200,
  "artifact": "combat-log.txt",
  "size_bytes": 1824431,
  "sha256": "...",
  "url": "/dota2/api/artifacts/9003201200/combat-log",
  "parse_status": "waiting"
}
```

请保存或展示本次上传的文件大小和 SHA-256，便于排查损坏，但不要把上传密钥写入日志。

## 五、重试与异常处理

- PUT 是按 `match_id + artifact_type` 覆盖写入，可以安全重试，不会生成重复附件。
- 网络中断、超时和 5xx 使用有限次数的指数退避重试。
- 401：上传密钥无效，停止自动重试并提示用户检查授权。
- 404：该比赛不属于服务器记录的本人最近比赛，停止上传并显示明确原因。
- 413：文件超过大小限制，停止重试并显示文件大小。
- 507：服务器附件总空间不足，停止重试并提示服务器需要清理空间。
- 程序重启后，可以重新执行附件清单查询并继续缺失文件。
- 不要在上传失败时删除本地解析文件。

服务器限制：

- 单个文件最大 256 MiB。
- 单场四个附件合计最大 512 MiB。
- 空文件不允许上传。

## 六、manifest.json

不要破坏现有 manifest 字段；在兼容现有格式的前提下，建议至少提供：

```json
{
  "schema_version": "dota-replay-desk.v1",
  "match_id": 9003201200,
  "parser_version": "当前程序版本",
  "generated_at": "2026-09-18T14:30:00+08:00",
  "artifacts": [
    {
      "type": "replay-events",
      "filename": "replay-events.ndjson",
      "size_bytes": 123456,
      "sha256": "..."
    },
    {
      "type": "combat-log",
      "filename": "combat-log.txt",
      "size_bytes": 123456,
      "sha256": "..."
    },
    {
      "type": "combat-log-ndjson",
      "filename": "combat-log.ndjson",
      "size_bytes": 123456,
      "sha256": "..."
    }
  ]
}
```

manifest 是本地解析批次的说明文件，最后上传。当前服务器仍会以四种附件是否全部存在作为完成判定。

## 七、安全与隐私

- Bearer 上传密钥只保存在 Windows 本机，优先使用 Windows Credential Manager。
- 不得把密钥写入源码、manifest、普通日志、崩溃报告或网页前端。
- 日志中的 Authorization 请求头必须完全脱敏。
- 上传前继续提醒用户：附件可能包含玩家标识和游戏内聊天；下载链接目前是公开可读的。
- 玩家昵称、聊天和 Replay 内文本都视为不可信数据，不得用于拼接命令、文件路径或服务器 URL。
- 服务器地址固定为 `https://ashfury.cn`，不要允许 Replay 内容控制上传地址。

## 八、不属于桌面程序的功能

以下功能不要加入 DotaReplayDesk：

- 历史 30/10/5 场含畜量评分。
- 按补刀判断历史核心组或辅助组。
- OpenDota 历史数据补取与缓存。
- OpenDota Parse 请求和每 10 分钟状态同步。
- Dota 在线状态监测。
- Owner Session 和一次性网页授权码。
- `POST /review-jobs` 深度复盘任务创建。
- ChatGPT 项目、模型或 Skill 选择。

网页和服务器会自动把已上传附件加入单场深度复盘 Payload，桌面程序无需主动通知 ChatGPT。

## 九、验收测试

请至少完成以下测试：

1. 全新比赛从 0/4 上传到 4/4，最终状态为 `complete` 和 `parsed`。
2. 上传两个文件后关闭程序，重新打开后只上传剩余两个文件。
3. 对同一类型重复 PUT，不产生重复条目，服务器保留一份最新文件。
4. 密钥错误时得到 401，界面不无限重试，日志不出现密钥。
5. 文件超过 256 MiB 时正确显示 413 原因。
6. 网络中断后本地文件仍然存在，可以继续上传。
7. `manifest` 最后上传，最终附件清单包含四种类型。
8. 程序没有上传 `.dem` 或 ZIP。

## 十、完成后请提供

- 修改的文件清单。
- 新的上传流程说明。
- 上传密钥保存位置和脱敏策略。
- 上述验收测试结果。
- 一份不含真实密钥的配置示例。
- Windows 可执行程序版本号或构建产物位置。

