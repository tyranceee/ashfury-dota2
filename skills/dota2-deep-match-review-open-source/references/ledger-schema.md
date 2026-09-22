# 版本2事实账本与机器验收

本文件是独立模式字段、数量和保存格式的唯一说明。分析方法见 full-review、职责和装备参考；覆盖与成稿验收见 submission-protocol。不要把这里的字段清单再抄进正文。

## 生成与迁移

```sh
python scripts/extract_match_facts.py match.json --user-account-id <账号ID> --output ledger.json
python scripts/extract_match_facts.py --check-ledger ledger.json --stage draft
python scripts/extract_match_facts.py --check-ledger ledger.json --stage final --review-markdown review.md
```

旧版账本没有结构化博弈记录，不能仅把版本号改成2。保留旧文件，从原始 JSON 生成新账本，再将有证据的分析迁入。不要把全部计数设为完成来绕过旧数据迁移。

脚本路径以本技能目录为基准；在其他工作目录运行时使用绝对路径。输出已存在时脚本拒绝覆盖，另选新文件名。生成规则更新后若旧账本与重新提取结果不一致，保留旧文件、从原始数据生成新账本，并复核迁入的分析，不手改事实摘要绕过检查。

生成文件包含：

- `schema_version: 2`、`user_account_id`。
- `source_match`：原始单场对象快照；`source_sha256`：该对象的稳定摘要，用于发现误编辑。它不是来源真实性的数字签名。
- `match`、`players`、`teamfights`、`objectives`、`death_timeline`、`roshan_aegis_lifecycles`：确定性提取结果，验收时由原始对象重建并核对。
- `parse_audit`：结构诊断；验收不信任手填的 `strict_complete`，而是重新检查原始对象。结构通过仍需人工检查时间范围与关键个人字段。
- 玩家 `field_status` 保留缺失/null/空/存在的区别；分钟曲线按 `times` 的实际秒数取值，无有效对齐样本时为 null；购买摘要单列，不充当逐笔购买事件。
- `analysis`：待填写的实际分析记录。
- `equipment_analysis_template`：逐件装备与持盾审计。
- `supplemental_sources`：可选的日志片段、录像观察或玩家复述。
- `preliminary_review`：收到初评时可手工加入的接收与核验记录，格式见 [preliminary-review.md](preliminary-review.md)。它不属于事实来源、不参与机器完成计数；版本2门禁不会审计其内部内容，必须人工检查。
- `analysis_coverage_template`：旧版展示字段，机器忽略其中的自报数量与状态。

账本包含原始数据与玩家标识，作为本地工作文件保存；公开发布只交接脱敏后的 Markdown。

## 每条分析的公共字段

每个 `analysis` 数组条目需要：

- `id`：本类内唯一的字符串。
- `status`：`complete`、`not_applicable`、`limited` 或 `pending`。完整复盘门禁仅允许前两项；`not_applicable` 另需 `reason`。
- 本类必填内容字段：实际分析文字，或明确的数据值。允许一句结论加已完成事件编号交叉引用，不重复抄事件经过；原始 evidence 仍须保留。
- `evidence`：来源与结论性质分开记录。

单个细节不可得时，填写 `无法确认：缺少具体什么记录`，或 `{"status":"unavailable","reason":"缺少技能目标时间戳"}`。不适用同理使用 `not_applicable` 并说明原因。只有“无法确认”“已完成”、空值或占位符不算内容。

整栏因证据不足而无法形成分析，应把记录状态设为 `limited`，交付部分复盘；不能将每个字段都标为未知后宣称完整。缺少录像才能获得的个别微观细节，可说明限制，不要求伪造。

证据对象示例（按实际数据替换引用）：

```json
{
  "source_type": "parsed_data",
  "judgment": "模型判断",
  "refs": ["/source_match/teamfights/0", "/source_match/objectives/1"],
  "limitations": ["团战记录是窗口汇总，没有逐次技能目标与效果持续时间"]
}
```

`source_type` 可为 `parsed_data`、`event_log`、`replay`、`player_recollection`、`mixed`。`judgment` 可为“数据明确显示”“模型判断”“高概率推断”“经验估计”“无法确认”“玩家复述”。“无法确认”需要非空的具体 `limitations`；纯玩家复述不能标为解析数据事实。

`refs` 使用 JSON Pointer，指向账本中的 `/source_match/...` 或 `/supplemental_sources/...`。数组下标从0开始。引用必须实际可解析，不得引用自己的分析段落来证明自己。多个独立事实支持概率推断时应分别引用；引用存在不代表它支持结论，仍须人工核对语义。

不得引用 `/preliminary_review/...` 或初评 Markdown 来证明比赛事实；不得把模型正文改标为 `event_log`、`replay` 或 `player_recollection` 塞入 `supplemental_sources`。初评指出的原始字段、玩家原话或网页只能作为检索线索，实际取得并核验后再按真实来源记录。两个模型对同一数据得出相同观点，不是两项独立证据。

全局与单波博弈的 `judgment` 必须属于模型判断、具充分证据的高概率推断或经验估计，不能使用“数据明确显示”表达整个战术假设。

## 全局与单波博弈

`analysis.global_gameplans` 保存2—3条，数组顺序为赛前预计重要程度。`core_question`、`resource`、`plan_a`、`plan_b`、`observable_signals` 表达基于阵容与机制的赛前推演，不能混入实际曲线、出装、团战或胜负结果。`actual_choices`、`result`、`adjustment`、`counterevidence`、`decision_quality` 记录实战核对和评价，正文在每点末尾简短呈现，不反向改写赛前前提。引用须区分确认阵容/补丁的前提依据与用于实战对照的事件依据；语义验收检查是否混用。以下字段也用于单波博弈，单波按该波开始时可知信息分析：

| 字段 | 内容 |
| --- | --- |
| `core_question` | 一个核心命题字符串 |
| `resource` | 双方争夺的核心资源 |
| `plan_a` | 甲方基于当时可知信息的合理计划和代价 |
| `plan_b` | 乙方自己的合理计划及双方反制关系 |
| `observable_signals` | 可以检验方案的技能、装备、目标或事件信号 |
| `actual_choices` | 记录支持的实际选择，不能伪造意图 |
| `result` | 结果与后续影响 |
| `adjustment` | 重打时改变的优先级、触发信号和代价 |
| `counterevidence` | 反证、替代解释或已检查但未发现反证的范围 |
| `decision_quality` | 基于当时信息评价，与最终胜负分开 |

`analysis.decisive_fights` 每条也有以上全部字段，另需：

- `start`、`end`：统一后的游戏秒数，时间窗不得重复，通常按游戏开始后的时间记录。
- 证据至少引用一条落在该时间窗内的事件或与其重叠的原始团战窗口；只引用英雄编号不能证明该波战斗存在。补充来源用作时间锚点时，必须是 `event_log` 或 `replay`，`time_basis` 为 `game_seconds`，引用对象包含数值 `time` 或 `start/end`。玩家回忆可补充语境，不能代替事件时间锚点。
- `global_gameplan_ids`：所检验的全局博弈 ID 数组；有关键团战时，每个全局博弈至少关联一波。
- `pre_fight`、`engagement`、`first_phase`、`second_phase`：战前、开团、第一与第二阶段；无法分阶段时写明证据限制。
- `engine_enabler`：发动机与赋能者关系。
- `resolution`、`map_conversion`、`contribution_and_error`：收尾、地图转化、主要贡献和失误。 `contribution_and_error` 中同时记录用户英雄的本波表现判断及依据，原始引用须能定位本人窗口记录；正文逐波呈现本人数据、职责与后果，不能仅用整场个人结论替代。

每波 `core_question` 只能是一个字符串，正文也只能突出一个核心命题。机器无法判断一个字符串是否暗藏多个并列命题，须在成稿语义核验时检查。

最终正文每波使用 `<!-- review-fight: ID -->` 标记，ID 与 `analysis.decisive_fights[].id` 一一对应且唯一。标记后放包含“天辉 X : Y 夜魇”本波英雄击杀数的标题，紧接参战数据表，再写“团战分析”“本人本波表现”和“本波总结”；列名与口径执行 full-review 模板。final 阶段检查逐波结构，draft 阶段仍检查原始分析字段。结构通过不证明数值、英雄映射或分析深度正确，须另做语义验收。

通常选择3—5波。实际数量在范围外时，填写 `analysis.fight_selection`：`reason`、`timeline_crosscheck`、`evidence`。须检查全局击杀、经济/经验与目标记录，说明确实不足或为什么必须多选；不能只因为解析器只列一波就假定只发生一波。零波时用全局计划内的实际事件检验博弈，并解释为什么没有决定性战斗。

## 其他必需分析数组

所有条目都需要公共字段和证据；下表内容字段均必填：

| 数组 | 身份与数量 | 内容字段 |
| --- | --- | --- |
| `lanes` | 三条，ID 为 `top`、`mid`、`bottom` | `matchup`, `pre_lane`（下述对象）, `minute_5_10`, `support_damage_0_6`, `early_events`, `rotation_boundary`, `minute_10_15`, `first_tower`, `conclusion`, `expectation_vs_actual` |
| `support_lane_pressure` | 四条，`player_index` 对应四辅助 | `baseline_0`, `cumulative_6`, `net_damage`, `lane_conversion` |
| `cores` | 六条，双方各三名，`player_index` 唯一 | `role_basis`, `primary_secondary_roles`, `enable_and_limit`, `economy_curve`, `lane_and_recovery`, `item_windows`, `participation_and_targets`, `key_skills`, `team_enabling`, `deaths_buybacks`, `map_conversion`, `conclusion`, `comparison` |
| `supports` | 四条，与核心合计覆盖十名玩家一次 | `role_basis`, `lane_conversion`, `vision`, `control_and_saves`, `key_deaths`, `equipment_fit`, `conclusion` |
| `key_skills` | 每方2—4条，`side` 为 `radiant` 或 `dire`；同方 `ability` 唯一 | `role`, `match_vs_window_uses`, `window_analysis`, `synergy`, `output_window`, `map_conversion` |
| `user_deaths` | 数量与源数据中用户实死次数一致 | `time`, `task_and_state`, `recorded_combatants`, `cause`, `visible_signals`, `choice`, `team_outcome`, `role_completion`, `classification`, `alternative` |
| `resource_categories` | 四条，ID 为 `damage_targets`、`buildings`、`roshan`、`buybacks` | `finding`, `consequence` |
| `training` | 1—3条，数组顺序即优先级 | `signal`（触发信号）, `action`（具体动作）, `tradeoff`（重要代价） |

核心、辅助与团战的内容标准以 full-review 和 submission-protocol 的完整清单为准；本表负责记录形状，不能取代正文分析。`cores` / `supports` 使用上表全部字段；`detail` 可省略或为 `detailed`。`detail=brief` 不能通过完整复盘验收；需要完整过程分析，不能只改标签。六核各有过程与同位置比较，四辅助各有职责分析，正文不能仅引用账本证明自己已经展开。

每条 `cores` / `supports` 的 `conclusion` 与其正文个人段落一致：按该英雄所在队伍的实际胜负，胜方写贡献判断及依据，败方写过错判断及依据或具体的无法归责理由。账本保持 cores/supports 两类记录；正文按玩家实际阵营组织为天辉五段、夜魇五段，合计覆盖六核与四辅助，将职责字段、过程字段与 conclusion 合写为该英雄的一段完整评价，不分别渲染成十人职责与十人贡献两轮分析，也不以账本行或评分表代替个人分析。该项属于语义验收，字段齐全不证明评价合理。

`window_analysis` 记录实际重要窗口及能力、使用证据、结果；没有相关事件时说明已查范围和限制，不要求一好一坏。兼容旧技能记录同时具备 `high_value_window` 与 `low_or_unrecorded_window`；新旧格式同时存在时以新字段验收，不用旧内容绕过新字段空白。

`training` 为内部记录键，正文展示为“本人对局改进意见”。使用公共 id/status/evidence；signal 交代用户本场具体时点、选择与信号，action 写本人可执行的替代处理，tradeoff 写代价，正文补明原选择后果并与记录一致。本人改进记录为必填内容，不得以改动源数据或版本号替代。

`player_index` 是源 `players` 数组的下标，不是账号 ID 或 `player_slot`。核心/辅助划分必须附职责判断依据，不声称是官方分配位置。中路没有辅助时，在对应字段写“不适用”及原因。

每路 `pre_lane` 必须单独保存对象，包含 `assumptions`（补丁、组合、技术/资源与支援前提）、`verdict`（哪方理论占优及条件）、`mechanisms`（关键机制依据）、`phase_windows`（等级及早期装备窗口）、`radiant_plan` 和 `dire_plan`（双方合理打法与反制），以上均为具体分析内容，另有独立 `evidence`。其 `judgment` 只允许“模型判断”“经验估计”或有具体限制的“无法确认”；不能标为数据事实。引用用于确认对位/补丁等前提，不能仅引用战后经济差证明理论优势；机制来源与推导依据在正文中说明，不伪造比赛字段。

`expectation_vs_actual` 单独说明实际表现相对理论预期的偏差与原因，不可用 `conclusion` 中的实际输赢替代。三路均须填写 `pre_lane` 与 `expectation_vs_actual`，并保留原始数据。机器核验缺项、内容类型和判断标签，正文是否真正包含这三段、是否用了事后结果倒推及理论是否合理仍由人工语义核查。

用户死亡不能用掉盾事件补足次数；来源未记录的实死需要补充日志，仍无法逐次确认时标明限制。`recorded_combatants` 记录可确认参战人数下限，不能用有伤害人数直接声称实际少打多。

## 装备记录

保留提取器生成的 `equipment_analysis_template` 结构。每名玩家、每件关键装备、每名黑皇杖持有者、每代不朽之守护及 `user_build_path` 都要填写 `evidence`，并保留 `evidence_level`。

十条 `player_item_audits` 均填 `player_index`, `role`, `build_conclusion`, `resource_fit`, `evidence_level`, `evidence`。`detail` 可省略或为 detailed，每人至少分析一件实际关键装备，并覆盖所有改变战斗结构的关键取舍；不能以 brief 或空列表跳过。路线分析解释能力顺序、相关战斗与目标结果。每人“最佳决定/最大问题”不强制填写；旧字段可保留。

每件 `key_items` 必填：`item`, `purchase_time`, `component_flow`, `capability_or_tradeoff`, `alternative_and_cost`, `targeting`, `prior_fight`, `first_relevant_fight`, `use_evidence`, `fight_outcome`, `team_enabling`, `map_conversion`, `window_judgment`, `evidence_level`, `evidence`。`alternative_and_cost` 必须比较一个现实替代与代价，原路线合理时说明替代为何更差；证据不足则说明具体缺口。此字段不可省略。其他字段对应装备参考中的组件流、购买前后战斗和因果链。

`bkb_player_audits` 每个确认持有者填 `player_index`, `purchase_time`, `window_analysis`, `conclusion`, `evidence_level`, `evidence`。仅分析实际重要窗口，不凑低价值样本。兼容旧记录同时具备 `high_value_window` 和 `low_value_or_uncovered_window`；新记录使用 `window_analysis`。

`aegis_lifecycle_audits` 保留提取出的每代身份/归属事实，另填 `first_relevant_fight`, `first_life_result`, `death_type_boundary`, `second_life_result`, `map_conversion`, `evidence_level`, `evidence`。

`user_build_path` 填 `first_fight_ready_item`, `enemy_problem_answered`, `highest_risk_item`, `item_gap_consequence`, `alternative_order`, `next_game_rule`, `evidence_level`, `evidence`。正常不存在的问题写有理由的不适用，不强找失误；局部建议不是要求再增加一条本人改进目标。

`high_risk_items_buyback_conflict` 与 `high_risk_items_buyback_evidence_level` 另配 `high_risk_items_buyback_evidence`。

每条 `analysis.cores` 另必填 `bkb_necessity`（是否该出、解决/未解决的具体威胁、履责窗口、条件与时机）和 `bkb_tradeoff`（不出的替代保护及可靠性、出的机会成本）。六核全部填写，不取决于是否买过；必要性文字标为模型判断，引用沿用该核心的真实证据。

装备模板的 `bkb_team_plan` 必须包含 `radiant`、`dire` 两个对象，各填 `allocation`（点名三核全出/部分出/暂缓及条件）、`tradeoff`（集体出装成本、保护资源分配及失效风险）和 `evidence`。判断标签只能为模型判断、经验估计或有理由的无法确认。六核必要性与双方配置均须填写，不修改原始数据。机器检查缺项与引用，团队方案是否合理、是否确实涵盖三核及正文是否呈现仍须人工核验。

黑皇杖最低审计名单从购买记录、整场使用和团战使用记录重建，不能通过清空 `required_bkb_player_indices` 删除。若库存或补充日志确认其他持有者，可加上有证据的额外审计；不得仅凭猜测补充。数字物品 ID 须使用与数据源匹配的映射。肉山代数及持盾身份由原始事件重新提取，不能改审计对象规避遗漏。

## 补充来源

`supplemental_sources` 是按来源 ID 索引的对象。每个来源保存 `source_type`（`event_log`、`replay` 或 `player_recollection`）、`match_id`、`locator`（文件路径或用户消息位置）、`time_basis` 和 `data`（实际引用片段或观察记录）。不要只填一个文件路径却不保留引用内容。

本地事件必须核对游戏秒数与开局前时间；同一事件的文本和 NDJSON 不重复计数。不同源冲突保留原值与原因，不能改 `source_match` 掩盖冲突。纯玩家回忆必须引用原话或准确转述，不能凭模型自己补写。录像观察应注明观看时间点和实际可见内容。

## 验收输出及边界

脚本输出 `passed`、`stage`、`allowed_status`、由实际数组计算的 `record_counts` 和具体 `missing`。成稿前通过才得到 `DRAFT_ALLOWED`；交付阶段还需要实际 Markdown 才能得到 `REVIEW_COMPLETE`。旧版自报状态不会影响结果。

缺字段、无效或重复主体、计数不足、无效来源引用、缺少单波命题、部分解析、待定栏目、空白交付章节都会阻止完整复盘门禁。正常不适用和带具体理由的局部证据限制可以记录；不能把这些机制当作省略分析的通道。

正文使用提交协议的七章节标题与顺序，旧15章结构保留兼容检查。包含准确 Match ID，不能只有标题。最后必须人工核对事实支撑、局部限制、中文名称、单一核心博弈、章节与账本一致性。机器验收不是战术正确性、证据因果关系或无幻觉的证明。
