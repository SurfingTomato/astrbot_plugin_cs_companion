# astrbot_plugin_cs_companion

**让你的大肥鱼能陪你看 CS 比赛。**

一个插件、六组能力、22 个工具，围绕一件事：让她在你旁边看比赛时**说得上话，而且不编数据**。

> Companion plugin for [AstrBot](https://github.com/AstrBotDevs/AstrBot) that lets a chat persona
> follow a CS2 tournament with you: live score injection, match-day radar, bet ledger,
> schedule/stat queries, player cards, and a post-match review that names a scapegoat with reasons.
> All data is sourced from **5Eplay** and **Liquipedia** — nothing is guessed.

`AstrBot 4.13+` · `Python ≥ 3.10` · `MIT`

---

## 快速开始

### 1. 装进去

从市场装：插件市场搜「CS 赛事陪看」，或右下角提交页填本仓库地址。

手动装（`git clone` 下来的目录名就是插件名，不用再挪）：

```bash
cd <AstrBot 数据目录>/data/plugins
git clone https://github.com/SurfingTomato/astrbot_plugin_cs_companion.git
```

### 2. 启用

面板（默认 `127.0.0.1:6185`）→ 插件管理 → 刷新 → 打开「CS 赛事陪看」。
没有面板就重启一次 AstrBot。

依赖只有 `aiohttp`，AstrBot 自带，不用另装。

### 3. 配两项（不配也能用，但少一半功能）

**① 推送会话** —— 陪看台的图间开口、开赛雷达的主动提醒都要往某个会话发消息，
需要 `unified_msg_origin`，格式形如 `platform:FriendMessage:123456789`。
不填就不推送（功能仍在，可以用 `cs_alert_recent` 查）。

**② Liquipedia 联系方式** —— 对方 API 条款要求 User-Agent 带**真实联系方式**，
且请求间隔不少于 2 秒。代码里已经做了 2.2 秒全局限速与 gzip，`contact` 这项要改成你自己的：

```json
{
  "watchparty": { "gap_umo": "aiocqhttp:FriendMessage:123456789" },
  "alert":      { "enable_push": true, "push_umo": "aiocqhttp:FriendMessage:123456789" },
  "player":     { "contact": "you@example.com" }
}
```

三个空组（`bet` / `review` / `schedule`）没有配置项，不用管。

### 4. 验证

随便发一句 `今天有什么比赛`，或者直接敲 `/watch`。能看到真实比分的场次列表就通了。

---

## 六组能力

| 能力 | 工具数 | 做什么 |
|---|---|---|
| **陪看台** | 5 | 把「正在看的那场」做成持续状态：每轮对话前自动注入真实比分与赛点，记录她的临场判断；图间休息时给她一份可抛的话题素材，并可按配置先开口 |
| **开赛雷达** | 2 | 后台 60 秒轮询，抓「开赛前 15 分钟 / 开打 / 一张图结束 / 赛点 / 结束」，同批事件合并推送；另有按悬念排序的今日推荐 |
| **打赌台** | 5 | 用真实赛前赔率开三档盘口（胜负 / 大比分 / 加时），赛后按真实赛果自动结算，累计积分、连胜与彩头 |
| **赛程簿** | 4 | 未来赛程（含 TBD 占位）、历史赛果与统计（地图胜率 / 回合差 / 场均击杀 / KD）、瑞士轮推演、胜率估算 |
| **选手簿** | 4 | 选手卡（真名 / 国籍 / 年龄 / 位置 / 换队履历）、首发五人 + 教练、近期人员进出、赛前对位 |
| **复盘台** | 2 | 赛后逐人 K-D-A / ADR / KAST / 评分 / 首杀差，按写死的判据判「扛把子 / 悲情英雄 / 背锅位 / 隐身」 |

全部工具：

```
陪看台   cs_watch_start  cs_watch_now  cs_watch_note  cs_watch_end  cs_gap_talk
雷达     cs_major_today  cs_alert_recent
打赌台   cs_bet_open     cs_bet_place  cs_bet_settle  cs_bet_board  cs_bet_forfeit
赛程簿   cs_schedule     cs_history    cs_swiss       cs_predict
选手簿   cs_player       cs_roster     cs_lineup_change  cs_prematch
复盘台   cs_review       cs_match_stats
```

指令：`/watch` `/csalert` `/bet` `/sched` `/player` `/roster` `/review`

### 从六个插件的旧版升级

早先这六组能力是六个独立插件（`astrbot_plugin_cs_watchparty` 等），现在合并成一个。
装新插件后请把旧的那六个**禁用或删掉**，否则 `/watch` 这类指令与 `cs_*` 工具会重名冲突。

运行期数据也换了位置：

```bash
# 旧：data/plugin_data/astrbot_plugin_cs_watchparty/watch_state.json
# 新：data/plugin_data/astrbot_plugin_cs_companion/watch_state.json

D=<AstrBot 数据目录>/data/plugin_data
mkdir -p "$D/astrbot_plugin_cs_companion"
cp "$D/astrbot_plugin_cs_watchparty/watch_state.json" "$D/astrbot_plugin_cs_companion/" 2>/dev/null
cp "$D/astrbot_plugin_cs_alert/alert_state.json"      "$D/astrbot_plugin_cs_companion/" 2>/dev/null
cp "$D/astrbot_plugin_cs_bet/bets.json"               "$D/astrbot_plugin_cs_companion/" 2>/dev/null
cp "$D/astrbot_plugin_cs_player/player_cache.json"    "$D/astrbot_plugin_cs_companion/" 2>/dev/null
```

不迁也能用，只是赌局积分、陪看状态和缓存会从零开始。

---

## 长什么样

<!--
截图位：建议放三张，命名 docs/img/watch.png / alert.png / review.png
1) /watch 后的对话，展示她带比分说话
2) 开赛雷达推送（多条合并成一条）
3) 复盘台输出（带判据）
放好文件后把下面每段的注释删掉、改成 ![说明](docs/img/xxx.png)
-->

完整样例见 [examples/sample_output.md](examples/sample_output.md)。

**赛后复盘**（真实数据，2026-11-23 Detonate 2-0 NXG）：

```
—— NXG（输，图分 0）——
  ★cow          26-30-7   ADR  84.5  KAST   64%  KD -4  首杀差 +3  评分 1.25  悲情英雄
   Ounce        22-28-5   ADR  58.5  KAST   56%  KD -6  首杀差 -3  评分 0.96  低迷
   Galaxy       12-30-10  ADR  56.8  KAST   58%  KD -18 首杀差 -2  评分 0.63  背锅位
  → 悲情英雄是 cow：26-30、ADR 84、评分 1.25，数据够了但队伍没赢下来
  → 背锅位是 Galaxy：12-30，评分只有 0.63、KAST 58%（回合参与不够）
```

**赛前对位**：

```
【FaZe】frozen(斯洛伐克)、jcobbb(波兰)、Twistzz(指挥·加拿大)、JBOEN(租借·丹麦)、Neityu(法国)
  · 平均在队 15 个月，最近加入 3 个月
【Liquid】NAF(加拿大)、EliGE(美国)、malbsMd(危地马拉)、Jorko(保加利亚)、JT(指挥·南非)
  · 平均在队 2.3 年  · 近 4 个月离队 2 人：siuhy→astralis、ultimate→apogee
· 默契：Liquid 平均 2.3 年，FaZe 平均 15 个月，后者的阵容更新。
```

---

## 为什么会有这个东西

看比赛时，AI 陪伴最容易犯三种错：

1. **不知道比分**，你一问她就开始播报，或者干脆编一句"现在好像 7:5"；
2. **不知道谁在打**，全程只会说"加油"，因为队里五个人她一个都不认识；
3. **赛后乱点人**，"XX 这场太菜了"——而她根本没有任何一个人的数据。

这套插件的全部设计都围着这三点：**能查的真查，查不到的明说不知道。**

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

## 目录结构

仓库根目录**就是插件本体**（市场按「一个仓库 = 一个插件」收录，`main.py` 与 `metadata.yaml` 必须在根）。

```
.
├── main.py                    # 唯一的 Star 类：30 个带装饰器的转发壳
├── cores/                     # 六组能力的实现，无装饰器
│   ├── watchparty.py          # 陪看台（含图间话题与比分注入）
│   ├── alert.py               # 开赛雷达
│   ├── bet.py                 # 打赌台
│   ├── schedule.py            # 赛程簿
│   ├── player.py              # 选手簿
│   └── review.py              # 复盘台
├── metadata.yaml              # 市场元数据
├── _conf_schema.json          # 分组配置：watchparty / alert / player
├── logo.png                   # 512×512 市场图标
├── tools/                     # 构建与验证脚本（不参与运行）
│   ├── make_cores.py          # plugins/*/main.py → cores/（去装饰器、去 Star 基类）
│   ├── make_main.py           # 核心类 → 转发壳
│   ├── make_logo.py           # 画图标
│   └── verify.py              # 注册表与 schema 一致性校验
├── plugins/                   # 六个独立插件原始版本，tools/ 的输入，别删
├── docs/DATA_SOURCES.md       # 接口字段、限速条款、无效路径
├── examples/sample_output.md  # 真实输出样例
└── LICENSE
```

### 为什么要有 `main.py` 这层转发壳

不是分层洁癖，是 AstrBot 的机制决定的：

- 插件实例的绑定用的是 `get_handlers_by_module_name(metadata.module_path)`，**按 handler
  定义处的模块路径精确匹配**。装饰器在 import 时就把 handler 记进全局注册表，并记下当时
  的 `__module__`。写在 `cores/xxx.py` 里的 handler 路径永远对不上插件主模块，等于没注册。
- 同理，一个模块里 import 多个 `Star` 子类也没用：`star_map` 以 `cls.__module__` 为键，
  同模块只保留第一个。

所以装饰器全部集中在根目录的 `main.py`，实现留在 `cores/`。转发壳的签名与 docstring 是从
原插件**逐字复制**的——`@llm_tool` 靠它们生成给模型看的 JSON Schema，改一个字符都会变样。
`tools/verify.py` 会拿旧版六个插件做基准逐字段比对，改完跑一遍就知道有没有动坏。

运行期数据落在 `data/plugin_data/astrbot_plugin_cs_companion/`：`watch_state.json`、
`alert_state.json`、`bets.json`、`player_cache.json`。这些都已在 `.gitignore` 里。

---

## 已知限制

- 瑞士轮后续轮次**官方不给对阵**，`cs_swiss` 是按 3 胜晋级 / 同战绩组内 Buchholz 高打低
  推算出来的，返回值里写明了"这是推算"；
- `cs_player` 的阵容来自百科快照，转会窗口期间可能滞后；它**不含任何比赛数据**；
- 复盘台依赖赛后数据落库，刚打完的几分钟内取不到，会自动往前翻到有数据的那一场；
- 六组能力共用一份配置，键名带组前缀；想单独关掉某组，目前只能靠人格里禁用对应工具。

---

## 参与

- 发现某场比赛数据取不到，欢迎开 issue 并附上**赛事链接与时间**，这类问题最好复现；
- 新玩法（更多图间话题、别的盘口、赛前情报卡）欢迎 PR。改 `cores/` 之后如果动了 handler 的
  签名或 docstring，记得重新生成转发壳，否则壳和实现对不上；
- 有两条线请不要动：**liquipedia 的 2.2 秒限速**、**复盘台的判据**。前者是对方的条款，
  后者是本项目"可对账"的承诺——想改判据可以加配置项，但默认值保留。
- 提交前跑这两条，本仓库不设 CI，靠它们兜底：

```bash
python -m py_compile main.py cores/*.py
python tools/verify.py new && python tools/verify.py diff    # 需要 AstrBot 运行环境
```

## 致谢

数据来自 [5Eplay](https://event.5eplay.com/csgo/matches) 与
[Liquipedia](https://liquipedia.net/counterstrike)（CC BY-SA 3.0）。
使用 Liquipedia API 请遵守其[条款](https://liquipedia.net/api-terms-of-use)：gzip、可识别 UA、
请求间隔 ≥ 2 秒。本仓库代码里已经照做，请不要把间隔改小。

## License

MIT
