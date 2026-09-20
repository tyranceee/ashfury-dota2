# ashfury 自适应 Dota 监控

生产方案：

- Steam Web API 每 600 秒检查目标账号是否正在运行 AppID 570；
- 仅在 Dota 在线时，每 10 秒查询 OpenDota 最新 Match ID；
- 新 Match ID 只登记一次，先保留 1 小时 DotaReplayDesk 本地上传窗口；
- 本地解析完整上传后立即标记为已解析，并跳过 OpenDota Parse；
- 1 小时后仍未解析时，最多提交一次 OpenDota Parse；提交后每 600 秒同步结果状态，绝不重复提交；
- Parse 数据只供“深度复盘”，历史画像只读取玩家历史基础行；
- 服务重启时从本地比赛索引预置基线，避免第一场新比赛被误当作旧基线；
- OpenDota `lane_role` 不作为 Valve assigned 1–5 真值；
- API Key 只保存在服务器 `/etc/ashfury/dota-monitor.env`，权限为 `600`。

生产服务：`ashfury-adaptive-watcher.service`。

公开状态接口：

- `https://ashfury.cn/dota2/api/monitor/status`
- `https://ashfury.cn/dota2/api/historical-profile/test/last10`

十局测试以最新一场天梯作为 Match X，严格排除 Match X，并选择在其开始前已经结束的最近 10 场天梯。测试不会对旧比赛提交真实 Parse。
