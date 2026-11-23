# astrbot_plugin_cs_companion

**让你的大肥鱼能陪你看 CS 比赛。**

六个插件、22 个工具，围绕一件事：
让她在你旁边看比赛时**说得上话，而且不编数据**。

> Companion plugins for [AstrBot](https://github.com/AstrBotDevs/AstrBot) that let a chat persona
> follow a CS2 tournament with you: live score injection, match-day radar, bet ledger,
> schedule/stat queries, player cards, and a post-match review that names a scapegoat with reasons.
> All data is sourced from **5Eplay** and **Liquipedia** — nothing is guessed.

---

## 为什么会有这个东西

看比赛时，AI 陪伴最容易犯三种错：

1. **不知道比分**，你一问她就开始播报，或者干脆编一句"现在好像 7:5"；
2. **不知道谁在打**，全程只会说"加油"，因为队里五个人她一个都不认识；
3. **赛后乱点人**，"XX 这场太菜了"——而她根本没有任何一个人的数据。

这套插件的全部设计都围着这三点：**能查的真查，查不到的明说不知道。**

---

## 六个插件

| 插件 | 工具数 | 做什么 |
|---|---|---|
| `cs_watchparty`<br>陪看台 | 5 | 把「正在看的那场」做成持续状态：每轮对话前自动注入真实比分与赛点，记录她的临场判断；图间休息时给她一份可抛的话题素材 |
| `cs_alert`<br>开赛雷达 | 2 | 后台 60 秒轮询，抓「开赛前 15 分钟 / 开打 / 一张图结束 / 赛点 / 结束」，同批事件合并推送；另有按悬念排序的今日推荐 |
| `cs_bet`<br>打赌台 | 5 | 用真实赛前赔率开三档盘口（胜负 / 大比分 / 加时），赛后按真实赛果自动结算，累计积分、连胜与彩头 |
| `cs_schedule`<br>赛程簿 | 4 | 未来赛程（含 TBD 占位）、历史赛果与统计（地图胜率 / 回合差 / 场均击杀 / KD）、瑞士轮推演、胜率估算 |
| `cs_player`<br>选手簿 | 4 | 选手卡（真名 / 国籍 / 年龄 / 位置 / 换队履历）、首发五人 + 教练、近期人员进出、赛前对位 |
| `cs_review`<br>复盘台 | 2 | 赛后逐人 K-D-A / ADR / KAST / 评分 / 首杀差，按写死的判据判「扛把子 / 悲情英雄 / 背锅位 / 隐身」 |

工具清单：

```
陪看台   cs_watch_start  cs_watch_now  cs_watch_note  cs_watch_end  cs_gap_talk
雷达     cs_major_today  cs_alert_recent
打赌台   cs_bet_open     cs_bet_place  cs_bet_settle  cs_bet_board  cs_bet_forfeit
赛程簿   cs_schedule     cs_history    cs_swiss       cs_predict
选手簿   cs_player       cs_roster     cs_lineup_change  cs_prematch
复盘台   cs_review       cs_match_stats
```

指令：`/watch` `/csalert` `/bet` `/sched` `/player` `/roster` `/review`

---

## 安装

```bash
cd <AstrBot 数据目录>/data/plugins
git clone https://github.com/<you>/astrbot_plugin_cs_companion.git
cp -r astrbot_plugin_cs_companion/plugins/* .
```

然后在面板（默认 `127.0.0.1:6185`）→ 插件管理 → 刷新 → 勾选这六个 → 重载。
没有面板就重启一次 AstrBot。

依赖：`aiohttp`（AstrBot 自带）。Python ≥ 3.10，AstrBot 4.13+。

### 首次必须配的两项

**① 推送会话（`cs_alert`、`cs_watchparty`）**

这两个插件会主动发消息，需要 `unified_msg_origin`。配置项名是 `push_umo` / `gap_umo`，
格式形如 `platform:FriendMessage:123456789`。不填就不会推送（插件仍会记录，可用
`cs_alert_recent` 查）。

**② Liquipedia 联系方式（`cs_player`）**

Liquipedia 的 API 条款要求 User-Agent 带**真实联系方式**，并且请求间隔不少于 2 秒。
`cs_player` 里已经做了 2.2 秒全局限速和 gzip，但 `contact` 这一项需要你改成自己的邮箱：

```json
{ "contact": "you@example.com" }
```

留默认占位值能用，但被限流的概率会上升。

---

## 数据源与边界

这是本项目的核心约束，也是它和"让 LLM 自由发挥"的区别。

| 数据 | 来源 | 覆盖 |
|---|---|---|
| 赛程 / 比分 / 逐图 | 5Eplay `session_list` | 未来两日 + 整届预排（TBD 占位）+ 可翻约 1 个月历史 |
| 逐人数据 | 5Eplay `session_info` | 每图每队 5 人：K/D/A、ADR、KAST、rating、首杀差、多杀、回合 MVP |
| 赔率 / 支持率 | 5Eplay `session_list` 内嵌字段 | 赛前胜率百分比 |
| 选手 / 阵容 / 换人 | Liquipedia `action=parse` | 百科快照，转会期可能滞后 |

**查不到、就明确说查不到的东西：**

- **HLTV**：Cloudflare 403，拿不到；
- **bo3.gg**：同样被拦；
- **5Eplay 的其它方法**（`match_detail` / `team_detail` / `player_list` 等）：一律
  `{"status":false,"error":"Unknown method"}`，只有 `session_list` 和 `session_info` 可用；
- 所以：**没有选手级实时数据流**，只有赛后落库的整场汇总。

接口细节、字段表、踩过的坑都记在 [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)。

### "不编数据"是怎么落到代码里的

- 比分、盘口、逐人数据**只从接口取**，取不到就返回一句"这场还没有逐人数据，别凭比分猜"；
- 图间话题只允许来自**真实比分**（刚打完那张图的图名与比分）或**固定常识卡池**
  （MR12、加时 MR3 先到 4 分、拆包 10 秒 / 5 秒这类不会过时的东西），具体选手数据一律不入池；
- 胜率有赔率就用赔率，没有才用近 14 天地图胜率做 logistic 估算，**并标明"估算"**，
  封顶 15%–85%，不说"稳赢"；
- 复盘台的判定规则写死在代码里、每次都连同判据一起打印出来，你能对上账：

```
输方 rating>=1.15 且（KD>0 或 rating>=1.25）→ 悲情英雄
rating<=0.85，或首杀差<=-3 且 KAST<=65%      → 背锅位
KAST<=60% 且 rating 0.85~1.05                → 隐身
赢方 rating>=1.25（或 >=1.10 且首杀差>=+3）  → 扛把子
```

一句话：**它只评价纸上写着的数字，不给选手加心理戏。**

---

## 长什么样

完整样例见 [examples/sample_output.md](examples/sample_output.md)。

赛后复盘（真实数据，2026-11-23 Detonate 2-0 NXG）：

```
—— NXG（输，图分 0）——
  ★cow          26-30-7   ADR  84.5  KAST   64%  KD -4  首杀差 +3  评分 1.25  悲情英雄
   Ounce        22-28-5   ADR  58.5  KAST   56%  KD -6  首杀差 -3  评分 0.96  低迷
   Galaxy       12-30-10  ADR  56.8  KAST   58%  KD -18 首杀差 -2  评分 0.63  背锅位
  → 悲情英雄是 cow：26-30、ADR 84、评分 1.25，数据够了但队伍没赢下来
  → 背锅位是 Galaxy：12-30，评分只有 0.63、KAST 58%（回合参与不够）
```

赛前对位：

```
【FaZe】frozen(斯洛伐克)、jcobbb(波兰)、Twistzz(指挥·加拿大)、JBOEN(租借·丹麦)、Neityu(法国)
  · 平均在队 15 个月，最近加入 3 个月
【Liquid】NAF(加拿大)、EliGE(美国)、malbsMd(危地马拉)、Jorko(保加利亚)、JT(指挥·南非)
  · 平均在队 2.3 年  · 近 4 个月离队 2 人：siuhy→astralis、ultimate→apogee
· 默契：Liquid 平均 2.3 年，FaZe 平均 15 个月，后者的阵容更新。
```

---

## 目录结构

```
.
├── plugins/
│   ├── astrbot_plugin_cs_watchparty/   # 陪看台（含图间话题）
│   ├── astrbot_plugin_cs_alert/        # 开赛雷达
│   ├── astrbot_plugin_cs_bet/          # 打赌台
│   ├── astrbot_plugin_cs_schedule/     # 赛程簿
│   ├── astrbot_plugin_cs_player/       # 选手簿
│   └── astrbot_plugin_cs_review/       # 复盘台
├── docs/DATA_SOURCES.md                # 接口字段、限速条款、无效路径
├── examples/sample_output.md           # 真实输出样例
└── LICENSE
```

运行期数据落在 `data/plugin_data/<插件名>/`：`watch_state.json`、`bets.json`、
`player_cache.json`、`alerts.json`。这些都已在 `.gitignore` 里。

---

## 已知限制

- 瑞士轮后续轮次**官方不给对阵**，`cs_swiss` 是按 3 胜晋级 / 同战绩组内 Buchholz 高打低
  推算出来的，返回值里写明了"这是推算"；
- `cs_player` 的阵容来自百科快照，转会窗口期间可能滞后；它**不含任何比赛数据**；
- 复盘台依赖赛后数据落库，刚打完的几分钟内取不到，会自动往前翻到有数据的那一场；
- 6 个插件是独立可拆的，但 `cs_watchparty` 的图间功能与 `cs_schedule` 的赛程口径一起用时体验最好。

## 致谢

数据来自 [5Eplay](https://event.5eplay.com/csgo/matches) 与
[Liquipedia](https://liquipedia.net/counterstrike)（CC BY-SA 3.0）。
使用 Liquipedia API 请遵守其[条款](https://liquipedia.net/api-terms-of-use)：gzip、可识别 UA、
请求间隔 ≥ 2 秒。本仓库代码里已经照做，请不要把间隔改小。

## License

MIT
