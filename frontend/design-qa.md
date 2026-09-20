# Design QA — A 版战术指挥台

- Visual source of truth: `/Users/mac/.codex/generated_images/01a0ad73-b49a-7c40-9ed2-1321097a8fa8/exec-9c5e2f33-3dd8-4569-837d-cadbd32268ee.png`
- Production URL: `https://ashfury.cn/dota2/`
- Verified match: `9001763544`
- Verification date: 2026-09-18 (Asia/Shanghai)
- Browser evidence viewport: 1490 × 769 px (Chrome desktop, 16:9-class landscape)
- Responsive check: 390 × 844 CSS px, device scale factor 1

## Evidence

- Overview viewport: `qa/v02-overview-viewport.jpg`
- Player profile dialog: `qa/v02-player-dialog.jpg`
- 16:9 baseline before correction: `qa/16x9-before.jpg`
- 16:9 post-fix production render: `qa/16x9-final-latest.jpg`

## Functional checks

- The selected match switches to `9001763544` without a page reload error.
- Exactly ten player rows render in two five-player tactical tables: the Owner, four allies, and five enemies.
- Every player row includes a hero image, Account ID, H/F/M values, C value when history is sufficient, tags, and one sentence of evidence-based summary.
- Account `180486487` opens a profile dialog showing H56 / F41 / M49 / C55 and the 30/10/5 windows.
- The Owner is included in the same scoring model and is marked “本人”.
- The public session does not render the deep-review trigger.
- The Owner-only trigger remains in the match action area beside the match selector and now reads “发动深度复盘”.
- The Data Center renders parse state and an explicit empty DotaReplayDesk attachment state without dead download links.
- The production document, hashed JavaScript/CSS assets, Owner-session endpoint, build, and site smoke suite all load successfully. Console inspection was unavailable in the Chrome native-app fallback used for this 16:9 pass; the earlier in-app-browser pass contained no warnings or errors.
- At 390 px, the document has no horizontal page overflow; each team table owns its horizontal inspection area.
- At 1490 × 769, all ten rows and the main conclusion remain inside the first viewport without horizontal page overflow.

## Data/model checks

- H and F use only ranked matches strictly completed before the selected match cutoff.
- The selected match ID is absent from every historical sample.
- M is calculated from the selected match's basic final fields as a separate layer.
- C uses 40% H + 25% recent risk derived from F + 35% current-match risk derived from M.
- No Replay Parse, teamfight, timeline, combat log, death-coordinate, or inferred 1–5 position field is used.
- Incomplete early participant snapshots retry after ten minutes; the newest match was successfully enriched from four to ten players.

## Visual comparison

- The implementation follows the selected A direction: black tactical-console shell, compact command header, teal/red team split, aligned comparison columns, strong score hierarchy, and dense but scannable evidence rows.
- The reference's sample score labels were replaced by real H/F/M/C values and evidence sentences; this is a product-model change, not a visual regression.
- The four chapters preserve the selected A command-table language while keeping Data Center and deep-review workflow separate.

## Severity assessment

- P0: none.
- P1: none.
- P2: resolved — the prior card layout was replaced by aligned tactical tables, allowing all ten players to remain readable in a 16:9 first viewport.
- P3: optional future polish — add real trend sparklines once the product decides which historical series should be exposed to the frontend.

final result: passed
