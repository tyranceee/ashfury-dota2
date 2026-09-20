# Owner 专属深度复盘触发

上线地址：`https://ashfury.cn/dota2/`

## 数据与安全边界

- 普通访客不登录，也看不到深度复盘按钮。
- Owner Session 是保存在浏览器中的随机不透明 Cookie；服务器只保存 SHA-256 哈希。
- Cookie：`Secure`、`HttpOnly`、`SameSite=Strict`、路径 `/dota2`，默认有效期 180 天。
- 一次性授权码默认 15 分钟过期且只能使用一次；同一来源 15 分钟内最多失败 10 次。
- `POST /review-jobs` 请求体严格只接受 `match_id`，额外 URL、Prompt、Shell Command 等字段直接返回 422。
- 同一 `match_id` 只有一个 job；重复提交返回已有 job，并重新签发一条 60 分钟有效的复盘指令。
- 每小时最多创建 3 个新比赛 job。
- signed payload 默认 60 分钟有效，只绑定一个 `public_job_id` 与一场比赛。
- Payload 删除比赛 JSON 中的昵称、队名及聊天字段，并明确标注附件内容仍可能含不可信聊天。
- 当前采用手动桌面启动：服务器准备固定指令，网页复制后由 Owner 打开 ChatGPT 桌面版并发送。
- 网页不会把“尝试打开桌面版”误判为“任务已经开始”；桌面协议不可用时保留 ChatGPT 网页版入口。

## 云端复盘的强制执行策略

目标配置固定为：

- ChatGPT 项目：`Dota`
- 项目 ID：`g-p-6aa78324e1f88191854e83883e96778e`
- 模型：`gpt-5.6-luna`
- 推理强度：`max`
- 必须使用的 Skill：`dota2-deep-match-review`
- 禁止任何降级或备用模型

网页服务器不会接受浏览器传入的项目、模型、Prompt、URL 或 Skill。它只根据 `match_id` 生成固定复盘指令。指令要求任务完整读取 Skill 及其指定的五份规则文件，在 Python 可用时先运行 `scripts/extract_match_facts.py`，然后完成“默认完整复盘”的所有必检项。Skill、模型或项目不满足时，任务必须停止并报告配置错误。

Payload 中也会携带同一份 `execution_policy`，供云端任务二次核对。

## 生成和使用授权码

服务器命令：

```bash
ssh aliyun-ecs
cd /opt/dota2-mcp
./.venv/bin/python generate_owner_code.py
```

然后在需要授权的 Mac 或 iPhone 浏览器打开：

`https://ashfury.cn/dota2/?owner=authorize`

输入刚生成的代码。每台设备/每个浏览器单独授权一次。

## API 验证

未授权创建任务应返回 403：

```bash
curl -i -X POST https://ashfury.cn/dota2/api/review-jobs \
  -H 'Content-Type: application/json' \
  --data '{"match_id":9000265617}'
```

命令行建立单独的测试 Owner Session：

```bash
curl -i -c owner.cookies -X POST https://ashfury.cn/dota2/api/owner-session \
  -H 'Origin: https://ashfury.cn' \
  -H 'Content-Type: application/json' \
  --data '{"code":"服务器生成的一次性授权码"}'
```

创建或读取幂等 job：

```bash
curl -i -b owner.cookies -X POST https://ashfury.cn/dota2/api/review-jobs \
  -H 'Origin: https://ashfury.cn' \
  -H 'Content-Type: application/json' \
  --data '{"match_id":9000265617}'
```

响应中的 `payload_url` 可以直接用 GET 测试：

```bash
curl '完整的 payload_url'
```

测试完成后删除 `owner.cookies`，或调用 `DELETE /dota2/api/owner-session` 撤销该测试会话。

## 桌面版使用流程

1. Owner 点击比赛页的“发动深度复盘”。
2. 服务器创建或复用该比赛的 job，并签发约 60 分钟有效的单场 Payload URL。
3. 页面自动复制服务器生成的固定指令并显示启动窗口。
4. 点击“打开 ChatGPT 桌面版”。进入 `Dota` 项目，选择 `gpt-5.6-luna`、`max`，粘贴并发送。
5. 如果 Windows 或 macOS 没有注册桌面协议，点击同一窗口中的“使用网页版”。

网页无法可靠读取外部桌面程序是否成功启动，也没有使用未公开的项目/消息深链。因此当前方案保证任务指令和数据权限正确，但不能替用户自动确认桌面 App 内的项目、模型与发送动作；这些不满足时，固定指令要求任务停止而不是降级。
