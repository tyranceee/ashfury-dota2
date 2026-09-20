import { useEffect, useMemo, useState } from "react";
import {
  Archive,
  ArrowRight,
  ArrowSquareOut,
  BookOpenText,
  Check,
  ClockCounterClockwise,
  Database,
  DownloadSimple,
  FileMagnifyingGlass,
  FileText,
  Flame,
  FolderOpen,
  MagnifyingGlass,
  Pulse,
  ShieldCheck,
  Sword,
  UserFocus,
  WarningCircle,
  X,
} from "@phosphor-icons/react";

const asset = (name) => `./assets/heroes/${name}.png`;
const heroNamesZh = {
  1: "敌法师", 5: "水晶室女", 12: "幻影长矛手", 18: "斯温", 26: "莱恩",
  28: "斯拉达", 35: "狙击手", 42: "冥魂大帝", 43: "死亡先知", 88: "司夜刺客",
  92: "维萨吉", 126: "虚无之灵",
};
const localHeroAssets = {
  1: asset("antimage"), 5: asset("crystal_maiden"), 12: asset("phantom_lancer"),
  18: asset("sven"), 26: asset("lion"), 28: asset("slardar"), 35: asset("sniper"),
  42: asset("skeleton_king"), 43: asset("death_prophet"), 88: asset("nyx_assassin"),
  92: asset("visage"), 126: asset("void_spirit"),
};
const fallbackMatches = [{
  id: "9001763544", result: "负", hero: "Visage", heroZh: "维萨吉",
  image: asset("visage"), kda: "6 / 13 / 28", duration: "56:49", status: "已解析",
}];
const reviewLibrary = [
  ["9000265617", "虚无之灵", "败线能修复，四打五要及时收手", "胜利"],
  ["8994420099", "斯温", "上高窗口正确，合流少了一步", "胜利"],
  ["8998580259", "穆尔塔", "不是没刷，是刷完没接管", "失败"],
  ["8997308782", "虚无之灵", "输出够高，进场还要更克制", "胜利"],
];
const artifactLabels = {
  "replay-events": "全量解析事件",
  "combat-log": "可读战斗日志",
  "combat-log-ndjson": "战斗日志 NDJSON",
  manifest: "解析清单与元数据",
};
const nav = [
  ["archive", "比赛档案", Archive],
  ["profiles", "玩家画像", UserFocus],
  ["reviews", "复盘报告", BookOpenText],
  ["data", "数据中心", Database],
];
const reviewIds = new Set(reviewLibrary.map(([id]) => id));

function normalizeMatch(match) {
  const id = String(match.match_id ?? match.id ?? "");
  const heroId = Number(match.hero_id || 0);
  return {
    id,
    startTime: Number(match.start_time || 0),
    result: match.win === true || match.result === "胜" ? "胜" : "负",
    hero: match.hero_name || match.hero || "Unknown Hero",
    heroZh: heroNamesZh[heroId] || match.hero_name || match.heroZh || "未知英雄",
    image: match.hero_image || match.image || localHeroAssets[heroId] || asset("visage"),
    kda: String(match.kda || "—").replaceAll("/", " / "),
    duration: match.duration || "—",
    status: match.parse_status === "parsed" || match.parsed
      ? "已解析"
      : match.parse_status === "parsing" ? "解析中" : "等待解析",
    review: reviewIds.has(id) ? "可查看" : "待生成",
  };
}

function formatMatchDate(timestamp) {
  if (!timestamp) return "比赛时间待同步";
  return new Intl.DateTimeFormat("zh-CN", {
    year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    hour12: false, timeZone: "Asia/Shanghai",
  }).format(new Date(timestamp * 1000));
}

function confidenceLabel(value) {
  return {
    medium: "中等置信度", medium_low: "中低置信度", low: "低置信度",
    insufficient: "数据不足",
  }[value] || "待评估";
}

function identityProfiles(workspace) {
  return [workspace?.self_participant, ...(workspace?.participants || [])]
    .filter(Boolean)
    .map((player) => ({
      account_id: player.account_id,
      hero_id: player.hero_id,
      hero_name: player.hero_name,
      hero_image: player.hero_image,
      relation: player.is_self ? "self" : player.relation,
      is_self: Boolean(player.is_self),
      sample_size: 0,
      confidence: "insufficient",
      scores: null,
      tags: ["画像载入中"],
      summary: "正在读取严格截止至本场开始前的公开天梯历史。",
      hero_pool: { unique_heroes: 0, top: [] },
    }));
}

function mergeProfiles(workspace, profileData) {
  const identities = identityProfiles(workspace);
  const scored = profileData?.profiles || [];
  if (!identities.length) return scored;
  const scoredByAccount = new Map(
    scored.filter((profile) => profile.account_id)
      .map((profile) => [profile.account_id, profile]),
  );
  const merged = identities.map((identity) => scoredByAccount.get(identity.account_id) || identity);
  const known = new Set(merged.map((profile) => profile.account_id).filter(Boolean));
  return [...merged, ...scored.filter((profile) => !known.has(profile.account_id))];
}

function playerName(profile) {
  return heroNamesZh[profile.hero_id] || profile.hero_name || "未知英雄";
}

function playerImage(profile) {
  return profile.hero_image || localHeroAssets[profile.hero_id] || asset("visage");
}

function scoreValue(profile, key) {
  return profile?.scores?.[key] ?? null;
}

function riskBand(score) {
  if (!Number.isFinite(score)) return "unknown";
  if (score >= 75) return "severe";
  if (score >= 55) return "high";
  if (score <= 25) return "low";
  return "medium";
}

function ScoreTriplet({ profile, compact = false }) {
  return (
    <div className={`score-triplet ${compact ? "compact" : ""}`}>
      {["S", "L", "F"].map((key) => (
        <div className={`score-chip score-${key.toLowerCase()}`} key={key}>
          <span>{key}</span><strong>{scoreValue(profile, key) ?? "—"}</strong>
        </div>
      ))}
    </div>
  );
}

function RiskPill({ profile }) {
  const score = scoreValue(profile, "C");
  return <span className={`risk-pill ${riskBand(score)}`}>{profile.tags?.[0] || "数据不足"}</span>;
}

function PlayerRow({ profile, onOpen }) {
  const composition = scoreValue(profile, "C");
  return (
    <button
      className={`player-row ${profile.is_self ? "self" : ""}`}
      onClick={() => onOpen(profile)}
      type="button"
    >
      <span className="player-cell identity-cell">
        <img src={playerImage(profile)} alt={`${playerName(profile)}英雄头像`} />
        <span><strong>{playerName(profile)}</strong><small>ID {profile.account_id || "匿名"}</small></span>
        {profile.is_self && <em>本人</em>}
      </span>
      {["S", "L", "F"].map((key) => (
        <span className={`metric-cell metric-${key.toLowerCase()}`} key={key}>
          <small>{key}</small><strong>{scoreValue(profile, key) ?? "—"}</strong>
        </span>
      ))}
      <span className={`composition-cell ${riskBand(composition)}`}>
        <strong>{composition ?? "—"}</strong>
        <i><b style={{ width: `${Number.isFinite(composition) ? composition : 0}%` }} /></i>
      </span>
      <RiskPill profile={profile} />
      <span className="evidence-cell">{profile.summary}</span>
      <ArrowRight className="row-arrow" size={17} />
    </button>
  );
}

function TeamTable({ kind, profiles, onOpen }) {
  const eligible = profiles.filter((profile) => scoreValue(profile, "L") != null).length;
  return (
    <section className={`team-table ${kind}`}>
      <header>
        <div><Sword size={21} weight="fill" /><strong>{kind === "ally" ? "我方阵容" : "敌方阵容"}</strong></div>
        <span>{profiles.length} 名玩家 · {eligible} 名可评分</span>
      </header>
      <div className="table-head">
        <span>英雄 / 玩家</span><span>S</span><span>L</span><span>F</span><span>C 含畜量</span><span>风险判断</span><span>关键证据</span><span />
      </div>
      <div className="player-rows">
        {profiles.map((profile, index) => (
          <PlayerRow
            profile={profile}
            onOpen={onOpen}
            key={profile.account_id || `${kind}-${index}`}
          />
        ))}
      </div>
    </section>
  );
}

function ModelNote({ benchmark }) {
  return (
    <section className="model-note">
      <div className="model-note-title"><FileText size={24} /><strong>评分范围</strong></div>
      <p>S20、L20、F10 与 F5 均严格截止到本场开始前。每场双方各自按补刀排序：前三名进入核心组、后两名进入辅助组，所有指标优先在同组比较；不展示或冒充真实1–5号位。</p>
      <div className="weight-strip">
        <span><b>30%</b> L20 历史风险</span>
        <span><b>50%</b> F10 近期风险</span>
        <span><b>20%</b> F5 短期风险</span>
        <span><b>{benchmark?.eligible_players ?? 0}/10</b> 可评分玩家</span>
      </div>
      <small>最终 C 将原始 30–70 风险区间线性展开到 0–100。</small>
      <small>L20：非线性胜负风险50% · 资源低效30% · 低影响力15% · 死亡风险5%；以50%胜率为基准，每多赢1场奖励递增，波动不计分。</small>
      <small>F10/F5：胜率40% · 影响力30% · 资源转化20% · 影响力稳定性10%；死亡不参与近期状态。</small>
    </section>
  );
}

function HistoricalArchive({ profiles, benchmark, loading, error, status, onOpen }) {
  const allies = profiles.filter((profile) => profile.relation === "self" || profile.relation === "ally");
  const enemies = profiles.filter((profile) => profile.relation === "enemy");
  return (
    <>
      <section className="command-heading">
        <div>
          <span>TACTICAL COMMAND TABLE · STRICT MATCH CUTOFF</span>
          <h1>双方阵容历史成分对照</h1>
          <p>以本场开始时间为截止线，只看双方十名玩家此前的历史实力、长期风险与近期状态。</p>
        </div>
        <div className="legend">
          <span><b>S</b>历史实力</span><span><b>L</b>历史风险</span><span><b>F</b>近期状态</span><span><b>C</b>含畜量</span>
        </div>
        {loading && <div className="loading-mark"><Pulse size={16} />画像计算中</div>}
        {!loading && status === "refreshing" && <div className="loading-mark"><Pulse size={16} />基础画像已显示，核心/辅助归一化正在后台补齐</div>}
        {!loading && status === "preparing" && <div className="loading-mark"><Pulse size={16} />首次生成画像中，完成后会自动刷新</div>}
        {error && <div className="error-mark"><WarningCircle size={16} />{error}</div>}
      </section>
      <section className="battle-board">
        <TeamTable kind="ally" profiles={allies} onOpen={onOpen} />
        <div className="versus-mark">VS</div>
        <TeamTable kind="enemy" profiles={enemies} onOpen={onOpen} />
      </section>
      <ModelNote benchmark={benchmark} />
    </>
  );
}

function MatchHeader({ match, workspace, monitor, owner, onReview, submitting, matches, onSelect }) {
  const parseState = workspace?.parse?.status === "parsed" ? "解析完成" : workspace?.parse?.status === "parsing" ? "解析中" : "等待解析";
  return (
    <section className="match-identity">
      <div className="match-number"><Archive size={28} /><div><span>比赛编号</span><strong>{match.id}</strong><small>{formatMatchDate(match.startTime)} · 天梯匹配</small></div></div>
      <div className="played-hero"><img src={match.image} alt={`${match.heroZh}英雄头像`} /><div><span>我使用的英雄</span><strong>{match.heroZh}</strong><em className={match.result === "胜" ? "win" : "loss"}>{match.result === "胜" ? "胜利" : "失利"}</em></div></div>
      <div className="match-stat"><span>K / D / A</span><strong>{match.kda}</strong></div>
      <div className="match-stat"><span>比赛时长</span><strong>{match.duration}</strong></div>
      <div className="match-progress">
        <div className="done"><Check size={15} weight="bold" /><span>已发现</span></div>
        <div className="done"><Check size={15} weight="bold" /><span>基础数据</span></div>
        <div className={workspace?.historical_profile?.status === "ready" ? "done" : "active"}><ClockCounterClockwise size={15} /><span>历史画像</span></div>
        <div className={workspace?.parse?.status === "parsed" ? "done" : "active"}><Database size={15} /><span>{parseState}</span></div>
      </div>
      <div className="match-actions">
        <label><span>切换比赛</span><select value={match.id} onChange={(event) => onSelect(event.target.value)}>{matches.map((item) => <option key={item.id} value={item.id}>{item.id}</option>)}</select></label>
        {owner && <button className="deep-review" onClick={onReview} disabled={submitting} title="为当前比赛创建一条深度复盘任务"><Flame size={18} weight="fill" />{submitting ? "正在创建任务…" : "发动深度复盘"}<ArrowRight size={17} /></button>}
        <div className={`monitor-state ${monitor?.dota_online ? "online" : "offline"}`}><i />{monitor?.dota_online ? "Dota 在线 · 10秒检测" : "Dota 离线 · 10分钟检测"}</div>
      </div>
    </section>
  );
}

function PlayerTile({ profile, onOpen }) {
  return (
    <button className={`profile-tile ${profile.relation || "unknown"}`} onClick={() => onOpen(profile)} type="button">
      <img src={playerImage(profile)} alt={`${playerName(profile)}英雄头像`} />
      <div><span>{profile.is_self ? "本人" : profile.relation === "enemy" ? "敌方" : "我方"}</span><h2>{playerName(profile)}</h2><small>ID {profile.account_id || "匿名"}</small></div>
      <ScoreTriplet profile={profile} compact />
      <div className="tile-composition"><span>C 含畜量</span><strong>{scoreValue(profile, "C") ?? "—"}</strong></div>
      <RiskPill profile={profile} />
      <p>{profile.summary}</p>
      <span className="open-profile"><FolderOpen size={15} />打开玩家档案</span>
    </button>
  );
}

function ChapterTabs({ view, setView }) {
  return (
    <nav className="chapter-tabs" aria-label="比赛工作台章节">
      {nav.map(([key, label, Icon]) => (
        <button key={key} className={view === key ? "active" : ""} onClick={() => setView(key)}><Icon size={18} /><strong>{label}</strong></button>
      ))}
    </nav>
  );
}

function PlayerDialog({ profile, onClose }) {
  if (!profile) return null;
  const windows = profile.windows || {};
  const historyWindowKey = windows.last_20 ? "last_20" : "last_30";
  return (
    <div className="dialog-backdrop" onMouseDown={onClose} role="presentation">
      <section className="player-dialog" onMouseDown={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-label={`${profile.account_id} 历史画像`}>
        <button className="close-button" onClick={onClose} aria-label="关闭"><X size={20} /></button>
        <img src={playerImage(profile)} alt={`${playerName(profile)}英雄头像`} />
        <div className="dialog-heading"><span>HISTORICAL PLAYER PROFILE</span><h2>{playerName(profile)}</h2><p>Account ID {profile.account_id}</p></div>
        <ScoreTriplet profile={profile} />
        <div className="dialog-composition"><span>C 含畜量</span><strong>{scoreValue(profile, "C") ?? "—"}</strong><RiskPill profile={profile} /></div>
        <blockquote>{profile.summary}</blockquote>
        <div className="window-stats">
          {[historyWindowKey, "last_10", "last_5"].map((key) => (
            <div key={key}><span>{key === "last_20" ? "最近20场" : key === "last_30" ? "旧版30场" : key === "last_10" ? "最近10场" : "最近5场观察"}</span><strong>{windows[key] ? `${windows[key].wins} 胜 · ${windows[key].win_rate}%` : "暂无数据"}</strong><small>{windows[key] ? `K/D/A ${windows[key].kills}/${windows[key].deaths}/${windows[key].assists}` : ""}</small></div>
          ))}
        </div>
        <div className="dialog-note"><ShieldCheck size={17} />截止至本场开始前 · 本场不进入历史样本 · {confidenceLabel(profile.confidence)}</div>
      </section>
    </div>
  );
}

function OwnerAuthorize({ onAuthorized, onClose }) {
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const submit = async (event) => {
    event.preventDefault(); setSubmitting(true); setError("");
    try {
      const response = await fetch("/dota2/api/owner-session", {
        method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "授权失败，请重新生成授权码");
      onAuthorized();
    } catch (caught) {
      setError(caught.message || "授权失败，请稍后重试");
    } finally { setSubmitting(false); }
  };
  return (
    <div className="dialog-backdrop"><section className="owner-dialog" role="dialog" aria-modal="true">
      <button className="close-button" onClick={onClose}><X size={20} /></button>
      <span>OWNER DEVICE AUTHORIZATION</span><h2>授权这台设备</h2>
      <p>输入服务器生成的一次性授权码。普通访客无需登录，也不会看到深度复盘按钮。</p>
      <form onSubmit={submit}><label htmlFor="owner-code">一次性授权码</label><input id="owner-code" value={code} onChange={(event) => setCode(event.target.value.toUpperCase())} placeholder="XXXX-XXXX-XXXX-XXXX" required /><button disabled={submitting}>{submitting ? "正在授权…" : "授权设备"}</button></form>
      {error && <div className="owner-error"><WarningCircle size={16} />{error}</div>}
      <div className="dialog-note"><ShieldCheck size={17} />使用 Secure、HttpOnly、SameSite=Strict Cookie。</div>
    </section></div>
  );
}

function ReviewLaunchDialog({ launch, onClose, onCopy }) {
  if (!launch) return null;
  return (
    <div className="dialog-backdrop" onMouseDown={onClose} role="presentation">
      <section className="owner-dialog review-launch-dialog" onMouseDown={(event) => event.stopPropagation()} role="dialog" aria-modal="true" aria-label="打开 ChatGPT 深度复盘">
        <button className="close-button" onClick={onClose} aria-label="关闭"><X size={20} /></button>
        <span>OWNER DEEP REVIEW</span><h2>复盘任务已经准备好</h2>
        <p>固定复盘指令已复制。打开 ChatGPT 后，请进入 <strong>Dota</strong> 项目，选择 <strong>GPT‑5.6 Luna · Max</strong>，粘贴并发送；任务会强制要求使用深度复盘 Skill。</p>
        <div className="launch-summary">
          <div><span>比赛</span><strong>{launch.matchId}</strong></div>
          <div><span>有效期</span><strong>约 60 分钟</strong></div>
        </div>
        <div className="launch-actions">
          <a className="desktop-launch" href={launch.desktopUrl}><Flame size={18} weight="fill" />打开 ChatGPT 桌面版</a>
          <a href={launch.webUrl} target="_blank" rel="noreferrer"><ArrowSquareOut size={18} />桌面版未打开？使用网页版</a>
          <button onClick={onCopy}><FileText size={18} />重新复制复盘指令</button>
        </div>
        <div className="dialog-note"><ShieldCheck size={17} />网页无法读取桌面程序是否成功启动；Windows 或 macOS 未注册桌面协议时，请使用网页版兜底。</div>
      </section>
    </div>
  );
}

function ProfilesView({ profiles, onOpen }) {
  return <section className="profile-ledger"><div className="section-intro"><span>PLAYER LEDGER</span><h1>逐人历史画像</h1><p>所有结论均来自赛前公开样本。点击任意玩家查看30/10/5场窗口。</p></div><div className="ledger-grid">{profiles.map((profile, index) => <PlayerTile key={profile.account_id || index} profile={profile} onOpen={onOpen} />)}</div></section>;
}

function ReviewsView({ matches, onSelect }) {
  return <section className="review-library"><div className="section-intro"><span>CURRENT MATCH ANALYSIS</span><h1>深度复盘报告</h1><p>这一章节只放当前比赛分析，与赛前历史画像严格分开。</p></div><div className="review-grid">{reviewLibrary.map(([id, hero, title, result]) => <article key={id}><span>{hero} · MATCH {id}</span><h2>{title}</h2><p>以证据区分个人责任、团队决策和无法从数据确认的部分。</p><div><em className={result === "胜利" ? "win" : "loss"}>{result}</em><a href={`/dota/reviews/${id}/`}>查看复盘 <ArrowRight size={15} /></a></div></article>)}</div><div className="recent-strip"><strong>最近比赛</strong>{matches.map((match) => <button key={match.id} onClick={() => onSelect(match.id)}>{match.id}<span>{match.heroZh} · {match.result}</span></button>)}</div></section>;
}

function DataView({ workspace, inventory, monitor }) {
  const parse = workspace?.parse || {};
  const files = Array.isArray(inventory?.artifacts) ? inventory.artifacts : [];
  const source = parse.source === "dota_replay_desk" ? "DotaReplayDesk" : parse.source === "opendota" ? "OpenDota" : "尚未取得";
  const status = parse.status === "parsed" ? "已解析" : parse.status === "parsing" ? "解析中" : parse.status === "failed" ? "解析失败" : "等待解析";
  return <section className="data-center"><div className="section-intro"><span>DATA PROVENANCE</span><h1>数据中心</h1><p>统一展示比赛基础数据、唯一解析结果和 DotaReplayDesk 附件。</p></div><div className="data-grid"><article><h2>当前比赛状态</h2><dl><div><dt>比赛 ID</dt><dd>{workspace?.match?.match_id || "—"}</dd></div><div><dt>统一解析状态</dt><dd>{status}</dd></div><div><dt>结果来源</dt><dd>{source}</dd></div><div><dt>历史画像</dt><dd>{workspace?.historical_profile?.status === "ready" ? "已缓存" : "按需计算"}</dd></div><div><dt>在线检测策略</dt><dd>{monitor?.dota_online ? "每10秒" : "每10分钟"}</dd></div></dl>{parse.result_url && <a className="data-link" href={parse.result_url} target="_blank" rel="noreferrer"><FileText size={17} />打开统一解析 JSON<ArrowSquareOut size={14} /></a>}</article><article><h2>本地解析附件</h2>{files.length ? <div className="artifact-list">{files.map((file) => <a key={file.type} href={file.url} target="_blank" rel="noreferrer"><FileText size={18} /><span><strong>{file.label || artifactLabels[file.type] || file.type}</strong><small>{file.filename || file.type}</small></span><DownloadSimple size={17} /></a>)}</div> : <div className="artifact-empty"><FileMagnifyingGlass size={25} /><span><strong>暂无本地附件</strong><small>DotaReplayDesk 上传后才会出现，不生成无效链接。</small></span></div>}<div className="privacy-note"><ShieldCheck size={18} />附件可能包含玩家标识或游戏内聊天，链接公开可读。</div></article></div></section>;
}

function App() {
  const [view, setView] = useState("archive");
  const [matches, setMatches] = useState(fallbackMatches);
  const [matchId, setMatchId] = useState(fallbackMatches[0].id);
  const [workspace, setWorkspace] = useState(null);
  const [profileData, setProfileData] = useState(null);
  const [profileLoading, setProfileLoading] = useState(false);
  const [profileError, setProfileError] = useState("");
  const [inventory, setInventory] = useState(null);
  const [monitor, setMonitor] = useState(null);
  const [owner, setOwner] = useState(false);
  const [selectedPlayer, setSelectedPlayer] = useState(null);
  const [notice, setNotice] = useState("");
  const [reviewSubmitting, setReviewSubmitting] = useState(false);
  const [reviewLaunch, setReviewLaunch] = useState(null);
  const [ownerAuthorizeOpen, setOwnerAuthorizeOpen] = useState(() => new URLSearchParams(window.location.search).get("owner") === "authorize");
  const flash = (message) => { setNotice(message); window.setTimeout(() => setNotice(""), 3000); };

  useEffect(() => {
    let cancelled = false;
    fetch("/dota2/api/owner-session", { cache: "no-store", credentials: "same-origin" })
      .then((response) => response.ok ? response.json() : { owner: false })
      .then((data) => { if (!cancelled) setOwner(data.owner === true); })
      .catch(() => { if (!cancelled) setOwner(false); });
    return () => { cancelled = true; };
  }, []);

  const loadMatch = async (id, announce = false) => {
    setProfileLoading(true); setProfileError(""); setProfileData(null); setInventory(null);
    try {
      const response = await fetch(`/dota2/api/v1/matches/${id}/workspace`, { cache: "no-store" });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      setWorkspace(data); setMatchId(String(id));
      const inventoryUrl = data.parse?.artifact_inventory_url || `/dota2/api/artifacts/${id}`;
      fetch(inventoryUrl, { cache: "no-store" }).then((result) => result.ok ? result.json() : null).then(setInventory).catch(() => setInventory(null));
      try {
        const profileResponse = await fetch(`/dota2/api/v1/matches/${id}/historical-profiles`, { cache: "no-store" });
        const profiles = await profileResponse.json().catch(() => ({}));
        if (!profileResponse.ok) throw new Error(profiles.detail || "历史画像暂不可用");
        setProfileData(profiles);
      } catch (caught) { setProfileError(caught.message || "历史画像暂不可用"); }
      if (announce) flash(`已切换到比赛 ${id}`);
    } catch { if (announce) flash(`未找到比赛 ${id}`); }
    finally { setProfileLoading(false); }
  };

  useEffect(() => {
    let cancelled = false;
    fetch("/dota2/api/matches?page=1&page_size=10", { cache: "no-store" })
      .then((response) => response.ok ? response.json() : Promise.reject())
      .then(async (data) => {
        if (cancelled || !data.matches?.length) return;
        const rows = data.matches.map(normalizeMatch); setMatches(rows); await loadMatch(rows[0].id);
      })
      .catch(() => loadMatch(fallbackMatches[0].id));
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!matchId || !["refreshing", "preparing"].includes(profileData?.status)) return undefined;
    let cancelled = false;
    let timer;
    const poll = async () => {
      try {
        const response = await fetch(`/dota2/api/v1/matches/${matchId}/historical-profiles`, { cache: "no-store" });
        const data = await response.json().catch(() => ({}));
        if (!cancelled && response.ok) {
          setProfileData(data);
          if (["refreshing", "preparing"].includes(data.status)) {
            timer = window.setTimeout(poll, 15000);
          }
        }
      } catch {
        if (!cancelled) timer = window.setTimeout(poll, 15000);
      }
    };
    timer = window.setTimeout(poll, 15000);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [matchId, profileData?.status]);

  useEffect(() => {
    let timer; let stopped = false;
    const refresh = async () => {
      let online = false;
      try {
        const response = await fetch("/dota2/api/monitor/status", { cache: "no-store" });
        if (response.ok && !stopped) { const data = await response.json(); online = data.dota_online === true; setMonitor(data); }
      } finally { if (!stopped) timer = window.setTimeout(refresh, online ? 10000 : 600000); }
    };
    refresh(); return () => { stopped = true; window.clearTimeout(timer); };
  }, []);

  const currentMatch = useMemo(() => workspace?.match ? normalizeMatch(workspace.match) : matches.find((match) => match.id === matchId) || matches[0], [workspace, matches, matchId]);
  const profiles = useMemo(() => mergeProfiles(workspace, profileData), [profileData, workspace]);
  const closeOwnerAuthorize = () => { setOwnerAuthorizeOpen(false); const url = new URL(window.location.href); url.searchParams.delete("owner"); window.history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`); };
  const createDeepReview = async () => {
    if (!owner || !currentMatch?.id || reviewSubmitting) return;
    setReviewSubmitting(true);
    try {
      const response = await fetch("/dota2/api/review-jobs", { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ match_id: Number(currentMatch.id) }) });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || "创建深度复盘任务失败");
      const instruction = data.trigger?.instruction;
      if (!instruction) throw new Error("服务器没有返回桌面复盘指令");
      let copied = false;
      try { await navigator.clipboard.writeText(instruction); copied = true; } catch { copied = false; }
      setReviewLaunch({
        instruction,
        matchId: data.match_id,
        desktopUrl: data.trigger.desktop_url || "codex://",
        webUrl: data.trigger.web_url || "https://chatgpt.com/",
      });
      flash(copied ? "复盘指令已复制，请打开 ChatGPT 桌面版" : "任务已准备，请点击“重新复制复盘指令”");
    } catch (caught) { if (String(caught.message).includes("Owner")) setOwner(false); flash(caught.message || "创建失败"); }
    finally { setReviewSubmitting(false); }
  };

  return (
    <div className="dossier-app">
      <header className="site-header">
        <a className="brand" href="/dota2/"><strong>ASHFURY</strong><span>/</span><em><Sword size={20} weight="fill" /> DOTA</em><small>TACTICAL COMMAND TABLE</small></a>
        <ChapterTabs view={view} setView={setView} />
        <div className={`service-state ${monitor?.dota_online ? "online" : "offline"}`}><i />{monitor?.dota_online ? "数据服务在线" : "低频待机"}</div>
        <form onSubmit={(event) => { event.preventDefault(); const value = new FormData(event.currentTarget).get("match"); if (value) loadMatch(value, true); }}><MagnifyingGlass size={15} /><input name="match" inputMode="numeric" placeholder="Match ID" /></form>
      </header>
      <MatchHeader match={currentMatch} workspace={workspace} monitor={monitor} owner={owner} onReview={createDeepReview} submitting={reviewSubmitting} matches={matches} onSelect={(id) => loadMatch(id, true)} />
      <main>
        {view === "archive" && <HistoricalArchive profiles={profiles} benchmark={profileData?.benchmark} loading={profileLoading} error={profileError} status={profileData?.status} onOpen={setSelectedPlayer} />}
        {view === "profiles" && <ProfilesView profiles={profiles} onOpen={setSelectedPlayer} />}
        {view === "reviews" && <ReviewsView matches={matches} onSelect={(id) => loadMatch(id, true)} />}
        {view === "data" && <DataView workspace={workspace} inventory={inventory} monitor={monitor} />}
      </main>
      <footer><span>ASHFURY.CN/DOTA2 · MATCH INTELLIGENCE</span><span>PLAY BETTER. WITH EVIDENCE.</span></footer>
      {notice && <div className="toast"><Check size={17} weight="bold" />{notice}</div>}
      <PlayerDialog profile={selectedPlayer} onClose={() => setSelectedPlayer(null)} />
      {ownerAuthorizeOpen && <OwnerAuthorize onAuthorized={() => { setOwner(true); closeOwnerAuthorize(); flash("设备授权成功"); }} onClose={closeOwnerAuthorize} />}
      <ReviewLaunchDialog launch={reviewLaunch} onClose={() => setReviewLaunch(null)} onCopy={async () => { try { await navigator.clipboard.writeText(reviewLaunch.instruction); flash("复盘指令已重新复制"); } catch { flash("浏览器未允许复制，请改用网页版"); } }} />
    </div>
  );
}

export { App };
