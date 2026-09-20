# 授权终端下载与 DeepSeek 初步解析

本文档对应 `backend/deploy/terminal_access.py`、`deepseek_review.py`、`deepseek_worker.py`、
`web_search.py` 与 `rest_server.py` 中新增的端点，以及前端「初步解析」章节。

## 一、授权终端下载解析后 JSON

### 访问通道

同一个端点支持两种凭据，互不影响：

| 通道 | 凭据 | 适用场景 | 跨源限制 |
| --- | --- | --- | --- |
| 终端令牌 | `Authorization: Bearer <token>` | curl、脚本、本机工具 | 无（令牌自带权限） |
| Owner 会话 | `ashfury_owner_session` Cookie | 网页里的下载按钮 | 仅同源，跨源一律 403 |

Bearer 令牌不能用来签发新令牌；签发与吊销只接受 Owner 会话。

### 权限范围

| scope | 允许的操作 |
| --- | --- |
| `parsed:read` | 列出并下载已解析比赛 JSON |
| `artifact:read` | 下载 DotaReplayDesk 上传的解析附件 |
| `review:read` | 列出并下载初步解析文件 |
| `review:enqueue` | 为单场比赛排队初步解析 |
| `admin` | 覆盖以上全部 |

### 端点

```
GET  /dota2/api/v1/terminal/capabilities                  自描述能力清单
GET  /dota2/api/v1/terminal/index?parsed_only=true        已解析比赛 + sha256/大小/下载地址
GET  /dota2/api/v1/terminal/parsed/{match_id}             下载单场 JSON（attachment）
GET  /dota2/api/v1/terminal/tokens                        Owner：列出令牌与审计尾部
POST /dota2/api/v1/terminal/tokens                        Owner：签发令牌（明文只返回一次）
DELETE /dota2/api/v1/terminal/tokens/{token_id}           Owner：吊销
POST /dota2/api/v1/terminal/tokens/revoke-all             Owner：全部吊销
```

### 签发与使用

```bash
# 1. 浏览器授权后，用 Owner 会话签发一个只读令牌
curl -s -X POST https://ashfury.cn/dota2/api/v1/terminal/tokens \
  -H 'Origin: https://ashfury.cn' \
  -H "Cookie: ashfury_owner_session=$OWNER_SESSION" \
  -H 'Content-Type: application/json' \
  -d '{"label":"mac-mini","scopes":["parsed:read","review:read"]}'
# 响应中的 plaintext_token 只出现这一次，服务器只保存 SHA-256

# 2. 终端下载
TOKEN=ashfury_xxx
curl -s https://ashfury.cn/dota2/api/v1/terminal/index \
  -H "Authorization: Bearer $TOKEN" | jq '.items[0]'

curl -sJO https://ashfury.cn/dota2/api/v1/terminal/parsed/9004864716 \
  -H "Authorization: Bearer $TOKEN"
# -> match_9004864716.json

# 3. 校验完整性
curl -s https://ashfury.cn/dota2/api/v1/terminal/index \
  -H "Authorization: Bearer $TOKEN" \
  | jq -r '.items[] | select(.match_id==9004864716) | .sha256'
shasum -a 256 match_9004864716.json
```

失败语义：凭证缺失或错误 → `401`；scope 不足 → `401` 且 detail 含 scope 名；
比赛未解析 → `409`；比赛不在最近 100 场内 → `404`。

### 凭据落盘与审计

- 令牌文件：`/opt/dota2-mcp/terminal-download-tokens.json`（`600`，只存哈希）
- 审计日志：`/opt/dota2-mcp/terminal-access-audit.ndjson`（JSON Lines，自动截断到 2000 条）
- 可用 `DOTA2_TERMINAL_TOKENS`、`DOTA2_TERMINAL_AUDIT` 覆盖路径

---

## 二、DeepSeek 初步解析

### 计费窗口（错峰 5 折）

DeepSeek 官方规则：**峰时为 UTC 01:00–04:00 与 06:00–10:00，周一至周五，
不含中国法定节假日**；其余时间全部按错峰计费。换算成北京时间：

| 时段 | 北京时间 |
| --- | --- |
| 峰时 | 09:00–12:00、14:00–18:00（周一至周五） |
| 错峰 | 00:00–09:00、12:00–14:00、18:00–24:00，外加周末与法定节假日全天 |

服务器只按 UTC 判断，北京时间仅用于展示。调度器行为：

1. 自动复盘开启时，只在错峰窗口内发起请求；
2. 若当前错峰窗口剩余时间不足 10 分钟，任务停在 `pending`，顺延到下一个足够长的错峰窗口；
3. 若一个窗口在任务执行途中关闭，剩余任务标记为 `paused_peak`，在下个窗口继续；
4. 峰时完全不发请求，因此不会产生峰时账单。

节假日日历内置在 `deepseek_review.py` 的 `BUILTIN_HOLIDAY_CALENDAR`，
可另放 `/opt/dota2-mcp/cn-public-holidays.json` 覆盖（`{"2027": ["2027-10-01", ...]}`）。
官方通知发布前，未知日期保守按峰时处理。

### 自动复盘范围

自动复盘只处理**最近 3 场已解析比赛**（`batch_size` 默认 3，可调 1–10）。
已经生成过初步解析的比赛仍占用名额，因此自动复盘不会向更早的比赛扩散；
需要更早的比赛时，在网页上对该场单独「单场排队」。

### 端点

```
GET  /dota2/api/v1/deepseek/status               状态、调度快照、设置、最近 job
GET  /dota2/api/v1/deepseek/schedule             错峰/峰时窗口与下次切换时间
PUT  /dota2/api/v1/deepseek/settings             Owner：自动复盘开关与参数
GET  /dota2/api/v1/deepseek/prompts              版本列表
POST /dota2/api/v1/deepseek/prompts              Owner：粘贴 Markdown 存为新版本
PUT  /dota2/api/v1/deepseek/prompts/active       Owner：切换生效版本
POST /dota2/api/v1/deepseek/preliminary-reviews  Owner：单场排队
GET  /dota2/api/v1/deepseek/run-now              Owner：立即跑一次调度
GET  /dota2/api/v1/deepseek/health               Owner：DeepSeek 凭据探活
GET  /dota2/api/v1/preliminary-reviews           已生成的初步解析索引
GET  /dota2/api/v1/preliminary-reviews/{id}      Owner：完整 JSON（含引用与计费）
GET  /dota2/api/v1/preliminary-reviews/{id}/json      下载 JSON
GET  /dota2/api/v1/preliminary-reviews/{id}/markdown  下载 Markdown
```

### 产物

- `/opt/dota2-mcp/preliminary_reviews/<match_id>.json`：正文 + 用量 + 计费 + 联网引用
- `/opt/dota2-mcp/preliminary_reviews/<match_id>.md`：可直接阅读的 Markdown
- 网页「已生成的初步解析」区每个文件都有 Markdown / JSON 一键下载按钮

---

## 三、联网搜索（重要限制）

DeepSeek 官方 API **没有服务端联网工具**：

- Chat Completions 文档：`tools` 中 *"Currently, only functions are supported as a tool."*
- Responses API 文档：`web_search` / `file_search` / `code_interpreter` / `mcp` 均在 Tools 表中标注 **Ignored**

因此联网通过服务器托管的 `web_search` function-calling 循环实现：

1. 服务器把 `web_search` 声明为 function 工具发送给 DeepSeek；
2. DeepSeek 需要最新事实时返回 tool_calls；
3. 服务器调用配置的搜索提供方（Tavily / Brave / SearXNG / 自定义）；
4. 结果作为 `tool` 消息回灌，最多往返 `max_search_calls` 次（默认 5）；
5. 所有引用（标题、URL、摘要、查询词）写入产物的 `web_search.citations`。

开启条件（两者都满足才生效）：网页打开「联网搜索」+ 服务器配置搜索凭据。

```bash
# /etc/systemd/system/dota2-rest.service.d/search.conf
[Service]
Environment=DOTA2_SEARCH_PROVIDER=tavily
Environment=DOTA2_SEARCH_API_KEY_FILE=/opt/dota2-mcp/search-api-key
Environment=DOTA2_SEARCH_MAX_CALLS=5
Environment=DOTA2_SEARCH_RESULTS=5
```

搜索结果属于不可信外部内容：只作为事实线索，其中的任何指令都不会被执行，
这一点在 system prompt 与产物 `trust_boundary` 中都有标注。

---

## 四、部署步骤

```bash
# 1. 后端源码
scp backend/deploy/{rest_server,terminal_access,deepseek_review,deepseek_worker,web_search}.py \
  aliyun-ecs:/opt/dota2-mcp/

# 2. DeepSeek API Key（600 权限，key 不经过对话）
ssh aliyun-ecs 'printf %s "sk-你的key" > /opt/dota2-mcp/deepseek-api-key && chmod 600 /opt/dota2-mcp/deepseek-api-key'

# 3. 重启服务
ssh aliyun-ecs 'sudo systemctl restart dota2-rest.service && systemctl is-active dota2-rest.service'

# 4. 前端
cd frontend && npm run build
scp -r dist/client/* aliyun-ecs:/usr/share/nginx/html/dota2/
```

## 五、环境变量汇总

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `DOTA2_TERMINAL_TOKENS` | `<BASE>/terminal-download-tokens.json` | 终端令牌文件 |
| `DOTA2_TERMINAL_AUDIT` | `<BASE>/terminal-access-audit.ndjson` | 审计日志 |
| `DOTA2_DEEPSEEK_PROMPT` | `<BASE>/deepseek-review-prompt.json` | Prompt 版本库 |
| `DOTA2_DEEPSEEK_JOBS` | `<BASE>/deepseek-review-jobs.json` | 任务队列与设置 |
| `DOTA2_DEEPSEEK_OUTPUTS` | `<BASE>/preliminary_reviews` | 初步解析产物目录 |
| `DOTA2_DEEPSEEK_API_KEY_FILE` | `<BASE>/deepseek-api-key` | DeepSeek API Key（须 `600`） |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | API 地址 |
| `DEEPSEEK_MODEL` | `deepseek-flash` | 默认模型 |
| `DOTA2_HOLIDAY_CALENDAR` | `<BASE>/cn-public-holidays.json` | 节假日日历覆盖 |
| `DOTA2_SEARCH_PROVIDER` | `tavily` | 搜索提供方 |
| `DOTA2_SEARCH_API_KEY` / `_FILE` | 空 | 搜索凭据 |
| `DOTA2_SEARCH_ENDPOINT` | 空 | SearXNG / 自定义端点 |
| `DOTA2_ALLOWED_ORIGINS` | 空 | 额外允许的写入源（本地开发用） |

## 六、测试

```bash
cd backend
.venv/bin/python -m unittest discover -p "test_*.py"
```

覆盖：错峰窗口算术与边界、节假日、Prompt 版本化、任务队列状态机、
worker 的窗口顺延与中途暂停、DeepSeek 请求报文与响应解析、
`web_search` 工具循环（含工具调用、迭代上限、搜索失败降级）、
终端令牌 scope/吊销/过期、以及新端点的端到端 401/403/409/scope 行为。
