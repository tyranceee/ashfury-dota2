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

### 上下文预算与实测成本

一场完整解析 JSON 约 300–550 KB。如果把它连同两场同批比赛一起发送，
单场请求约 46.8 万 input token。因此默认使用 `companion_detail=compact`：
同批比赛改发「紧凑视图」——主人自己的完整行、十人计分板、目标事件、
抽样的经济曲线，以及**主人所在位置在每一波团战里的技能/物品使用、击杀、
治疗与金钱变化**（这是复盘最常引用的证据）。实测紧凑视图约为完整 JSON 的
10.6%，同时保留关键证据。

实测（deepseek-flash，错峰，最近 3 场一批）：

| 配置 | 单场 input token | 单场成本 | 3 场合计 |
| --- | --- | --- | --- |
| 同批发完整 JSON | ~468,000 | ~$0.075 | $0.2244 |
| 同批发紧凑视图（默认） | ~187,000 | ~$0.012 | $0.0347 |

也就是**每场约 1 美分**。想进一步省钱可以：

- `reasoning_effort` 改为 `low` 或 `none`（输出 token 会明显下降）；
- `batch_size` 改为 1（只算最近一场，不再附同批对比）；
- `context_budget_chars` 调小（默认 700000，超出时优先丢弃同批比赛）。

同批比赛只用于横向对比，所以丢弃它们不会影响本场结论。

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

## 三、联网搜索

### 为什么需要 Provider 架构

DeepSeek 的两套原生接口**不执行**服务端搜索，实测证据：

| 调用方式 | 结果 |
| --- | --- |
| `POST /chat/completions` + `tools:[{"type":"web_search"}]` | `422`：`unknown variant \`web_search\`, expected \`function\`` |
| `POST /responses` + `tools:[{"type":"web_search"}]` | `200` 但**静默忽略**，模型回答"我当前没有联网搜索的能力" |
| `POST /responses` + `search:{enabled:true}` | 同样静默忽略 |

但 DeepSeek 的 **Anthropic 兼容接口支持服务端搜索工具**：

| 调用方式 | 结果 |
| --- | --- |
| `POST /anthropic/v1/messages` + `tools:[{"type":"web_search_20250305"}]` | `200`，返回 `web_search_tool_result` 内容块与 `usage.server_tool_use.web_search_requests` |

所以默认提供方是 **`deepseek-hosted-search`（DeepSeek 托管搜索）**，
走 Anthropic 兼容端点，**不需要任何第三方搜索账号**，用的是同一把 DeepSeek Key。

### Provider 清单与选择顺序

| provider | 端点 | 凭据 |
| --- | --- | --- |
| `deepseek-hosted-search`（默认） | `api.deepseek.com/anthropic/v1/messages` | DeepSeek Key |
| `tavily` | `api.tavily.com/search` | `DOTA2_SEARCH_API_KEY` |
| `brave` | `api.search.brave.com/res/v1/web/search` | `DOTA2_SEARCH_API_KEY` |
| `searxng` | 自建实例 | `DOTA2_SEARCH_ENDPOINT` |
| `custom` | 任意 HTTP JSON | `DOTA2_SEARCH_ENDPOINT` |

`DOTA2_SEARCH_PROVIDER` 指定首选；若首选缺凭据，会自动回退到任一可用提供方，
并在产物的 `web_search.warnings` 里记录回退原因。没有可用提供方时**明确报错**，
不会假装搜索成功。

### 搜索词的完整性

搜索词只能由上游（初步解析的 function-calling 循环 / 单场排队）给出，
执行层原样发送：

- system prompt 固定为"必须原样使用用户提供的搜索词调用一次 web_search，
  不得翻译、改写、扩展或删减"；
- 请求体用 `<exact_query>…</exact_query>` 包裹，工具 `max_uses=1`；
- 返回后校验 `server_tool_use.input.query` 是否与发送值**完全相等**，
  不等就抛 `SearchQueryRewritten` **拒绝本次结果**（不是降级接受）。

### 结果校验（进入流程前）

每条结果都按顺序过一遍：

1. **URL 安全性**：仅允许 `https`；拒绝 `file:`/`data:`/`javascript:` 等协议、
   私网与回环 IP（含 `169.254.169.254` 云元数据地址）、`localhost`、
   `*.internal`、`*.local`、`*.onion`。不安全的结果**保留并标记**
   `unsafe_url`/`unsafe_reason` 并计入 `dropped`，但任何调用方都不得当作可用来源。
2. **授权域名**：配置 `DOTA2_SEARCH_ALLOWED_DOMAINS` 时，同时传给服务端工具的
   `allowed_domains` 并在本地二次过滤。
3. **去重**：URL 归一化（去 fragment、去 `utm_*`/`fbclid` 等追踪参数、
   `www.` 前缀、大小写、末尾斜杠）后判重，另加同域标题 shingle Jaccard ≥0.9 的近似判重。
4. **来源独立性**：重复项标记 `is_independent_source=false` 并记录
   `duplicate_of`；重复转载**不**算作独立佐证。

### 返回字段

每次搜索记录为一个 `searches[]` 条目，并在 `citations[]` 展平所有来源：

```json
{
  "query": "Dota 2 7.41 潮汐猎人 改动",
  "provider_id": "deepseek-hosted-search",
  "queries_executed": ["Dota 2 7.41 潮汐猎人 改动"],
  "result_count": 10,
  "cost": {"currency": "CNY", "cost_cny": 0.0143, "web_search_requests": 1},
  "dropped": {"unsafe_url": 1, "domain_not_allowed": 0,
              "exact_duplicate": 0, "near_duplicate": 0, "invalid": 0},
  "results": [{
    "title": "...", "url": "https://...", "snippet": "",
    "published_at": "2026-03-25", "source_service": "deepseek-hosted-search",
    "domain": "liquipedia.net", "is_independent_source": true, "duplicate_of": null
  }],
  "raw_response": "...（完整原始响应，含 include_raw=true 时输出）"
}
```

`snippet` 通常为空：DeepSeek 托管搜索只返回标题、URL 与 `page_age`，
不返回网页正文摘要。需要正文时应另行抓取该 URL，不要把标题当摘要使用。

### 实测

生产环境单场复盘（潮汐猎人）：

- 实际执行 6 次联网搜索，每次返回 10 条结果；
- 其中 2 条命中 URL 安全过滤（`unsafe_url`）被标记；
- 引用覆盖中文、俄文、英文、法文来源；
- 搜索费用 ¥0.0607，模型费用 ¥0.3438，单场合计 **¥0.4045**。

搜索会打断上下文缓存，因此开启联网后单场费用高于纯本地解析（约 ¥0.012）。
可以用 `max_search_calls`（默认 5，上例设为 4 仍产生 6 次）控制上限，
或在地图/机制类问题较多时保持开启、纯数据复盘时临时关闭。

## 四、部署步骤

```bash
# 1. 后端源码
scp backend/deploy/{rest_server,terminal_access,deepseek_review,deepseek_worker,web_search}.py \
  aliyun-ecs:/opt/dota2-mcp/

# 2. DeepSeek API Key（600 权限，key 不经过对话）
#    这一把 key 同时用于初步解析与默认的托管搜索，不需要第三方搜索账号。
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
| `DOTA2_SEARCH_PROVIDER` | `deepseek-hosted-search` | 搜索提供方（可换 tavily/brave/searxng/custom） |
| `DOTA2_SEARCH_API_KEY` / `_FILE` | 空 | 搜索凭据 |
| `DOTA2_SEARCH_ENDPOINT` | 空 | SearXNG / 自定义端点 |
| `DOTA2_SEARCH_ALLOWED_DOMAINS` | 空 | 逗号分隔的授权域名 |
| `DOTA2_SEARCH_RESULTS` | `5` | 每次搜索期望结果数 |
| `DEEPSEEK_ANTHROPIC_URL` | `https://api.deepseek.com/anthropic/v1/messages` | 托管搜索端点 |
| `DEEPSEEK_SEARCH_MODEL` | `deepseek-flash` | 托管搜索使用的模型 |
| `DOTA2_ALLOWED_ORIGINS` | 空 | 额外允许的写入源（本地开发用） |
| `DOTA2_ARTIFACT_TZ_OFFSET` | `8` | 产物时间戳的时区偏移（小时） |
| `DOTA2_ARTIFACT_TZ_LABEL` | `北京时间` | 产物时间戳的时区标签 |
| `DOTA2_CONTEXT_BUDGET_CHARS` | `700000` | 默认上下文预算（可被设置覆盖） |
| `DOTA2_ACCOUNT_ID` | `212121467` | 用于在解析数据里定位主人自己的行 |

## 六、产物内容

Markdown 产物结尾会写明生成时间（带明确时区，不使用宿主机的 `localtime`，
因为容器可能报告 CST 而进程仍按 UTC 运行）、模型、计费窗口、Prompt 版本与总 token。

JSON 产物除正文外还包含：

- `usage` / `cost`：token 明细与按错峰或峰时费率计算的估算成本；
- `context`：同批比赛是否进入、是否因预算被裁剪、本场是否被截断、prompt 字符数；
- `web_search`：是否启用、是否可用、实际调用轮数与全部引用；
- `trust_boundary`：数据被当作不可信内容处理的标记。

`/v1/deepseek/status` 也会返回 `stats.estimated_total_usd`，方便长期观察花费。

## 七、测试

```bash
cd backend
.venv/bin/python -m unittest discover -p "test_*.py"
```

覆盖：错峰窗口算术与边界、节假日、Prompt 版本化、任务队列状态机、
worker 的窗口顺延与中途暂停、DeepSeek 请求报文与响应解析、
`web_search` 工具循环（含工具调用、迭代上限、搜索失败降级）、
终端令牌 scope/吊销/过期、以及新端点的端到端 401/403/409/scope 行为。
