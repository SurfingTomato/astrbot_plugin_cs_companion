# 真实输出样例

以下都是直接跑出来的，没有手改数字。时间：2026-11-23。

---

## 1. `cs_review` 赛后复盘

```
【赛后复盘】Detonate 2-0 NXG　BO3　11-23 10:00　第1轮
逐图：Inferno 13:3｜Cache 13:7

—— Detonate（赢，图分 2）——
  ★CoolComs     35-15-13  ADR 101.1  KAST   92%  KD +20  首杀差 +1  评分 1.88  扛把子
  ★Pose1doNN    35-16-6   ADR  90.4  KAST   89%  KD +19  首杀差 +3  评分 1.70  扛把子
   CrePoW       29-22-11  ADR  95.9  KAST   86%  KD +7  首杀差 +2  评分 1.17  中规中矩
   SeRCEra      31-25-6   ADR  81.3  KAST   72%  KD +6  首杀差 -1  评分 1.12  中规中矩
   tatm         21-17-13  ADR  61.5  KAST   83%  KD +4  首杀差 +3  评分 1.07  中规中矩
  → 这场是 CoolComs 在扛（评分 1.88）

—— NXG（输，图分 0）——
  ★cow          26-30-7   ADR  84.5  KAST   64%  KD -4  首杀差 +3  评分 1.25  悲情英雄
   Ounce        22-28-5   ADR  58.5  KAST   56%  KD -6  首杀差 -3  评分 0.96  低迷
   PinheadLarry 19-33-3   ADR  57.5  KAST   58%  KD -14 首杀差 -1  评分 0.71  低迷
   Leodyn       15-31-4   ADR  44.3  KAST   56%  KD -16 首杀差 -5  评分 0.69  低迷
   Galaxy       12-30-10  ADR  56.8  KAST   58%  KD -18 首杀差 -2  评分 0.63  背锅位
  → 悲情英雄是 cow：26-30、ADR 84、评分 1.25，数据够了但队伍没赢下来
  → 背锅位是 Galaxy：12-30，评分只有 0.63、KAST 58%（回合参与不够）

判据：赢方 rating>=1.25（或 >=1.10 且首杀差>=+3）→ 扛把子；输方 rating>=1.15 且
（KD>0 或 rating>=1.25）→ 悲情英雄；rating<=0.85，或首杀差<=-3 且 KAST<=65% → 背锅位；
KAST<=60% 且 rating 0.85~1.05 → 隐身；其余中规中矩。
（数据来自 5Eplay 官方赛事数据，评分是 5E 自己的算法，不是 HLTV Rating。
只评价这张纸上有的东西，别给人加心理戏。）
```

> 注意 `cow` 是 KD 负数但评分 1.25——ADR 84.5 撑着。判据允许这种情况当"悲情英雄"，
> 因为它是唯一一个数据对得起自己的人。这种细节就是判据必须打印出来的原因。

---

## 2. `cs_match_stats` 纯数据表

```
【逐人数据】Detonate vs NXG　11-23 10:00
逐图：Inferno 13:3｜Cache 13:7

选手             K   D   A    ADR   KAST   KD差   首杀   多杀  MVP    评分
— Detonate —
CoolComs      35  15  13  101.1    92%   +20    4    4    6  1.88
Pose1doNN     35  16   6   90.4    89%   +19    5    4    7  1.70
CrePoW        29  22  11   95.9    86%    +7    8    1    5  1.17
SeRCEra       31  25   6   81.3    72%    +6    2    2    5  1.12
tatm          21  17  13   61.5    83%    +4    3    1    3  1.07
— NXG —
cow           26  30   7   84.5    64%    -4    5    0    3  1.25
Ounce         22  28   5   58.5    56%    -6    2    2    2  0.96
PinheadLarry  19  33   3   57.5    58%   -14    4    1    4  0.71
Leodyn        15  31   4   44.3    56%   -16    1    1    1  0.69
Galaxy        12  30  10   56.8    58%   -18    2    0    0  0.63
```

---

## 3. `cs_schedule` 未来赛程

```
11-24
　12:00 9z vs LVG　BO1　小组赛 第一轮　65%/35%　[cn出征]
　12:00 Heroic vs Imperial　BO1　小组赛 第一轮
　14:00 Liquid vs BC.Game　BO1　小组赛 第一轮　53%/47%　[s1mple]
　15:00 FaZe vs Wildcard　BO1　小组赛 第一轮　64%/36%
　17:00 小组赛 第二轮　对阵未定（TBD）
11-25
　小组赛 第三轮　对阵未定（TBD）
11-28 第一轮［第2阶段］
```

瑞士轮后续轮次在赛前就是 TBD，官方不提前公布配对——所以这里不猜，只标占位。

---

## 4. `cs_prematch` 赛前对位

```
【赛前对位】FaZe vs Liquid
【FaZe】
  frozen(斯洛伐克)、jcobbb(波兰)、Twistzz(指挥·加拿大)、JBOEN(租借·丹麦)、Neityu(法国)
  · 平均在队 15 个月，最久 3.0 年，最近加入 3 个月
  · 教练 enkay J（德国）
【Liquid】
  NAF(加拿大)、EliGE(美国)、malbsMd(危地马拉)、Jorko(保加利亚)、JT(指挥·南非)
  · 平均在队 2.3 年，最久 8.8 年，最近加入 4 个月
  · 教练 flashie（匈牙利）
  · 近 4 个月离队 2 人：siuhy→astralis、ultimate→apogee
· 默契：Liquid 平均 2.3 年，FaZe 平均 15 个月，后者的阵容更新。
（名单与在队时长来自 Liquipedia。这是履历，不是实力判断——
别拿它推谁赢，也别编选手的 rating 或数据。）
```

---

## 5. `cs_player` 选手卡

```
【s1mple】Oleksandr Olehovych Kostyliev｜乌克兰
· 1997-10-02 生（29 岁）｜步枪、狙击位｜Active｜活跃 2013 – Present
· 现在在 BC.Game Esports
· 外号：The Undertaker, s1s1
· 履历（近几段）：
    2023-10-26 — 2025-07-28 → Natus Vincere（Inactive）
    2024-09-27 — 2024-11-21 → Team Falcons（Loan）
    2025-05-05 — 2025-07-09 → FaZe Clan（Loan）
    2025-07-28 — Present → BC.Game Esports
    （总共 21 段，含租借/替补）
· 背景：Oleksandr "s1mple" Kostyliev is a Ukrainian professional Counter-Strike 2 player …
```

---

## 6. `cs_roster` 首发名单

```
【LVG】当前首发
  westmelon（指挥·中国） Niu Zhe — 入队 2017-05-24（9.5 年）
  z4kr（中国） Zhang Sike — 入队 2019-08-09（7.3 年）
  EmiliaQAQ（中国） Tang Junjie — 入队 2023-01-22（3.8 年）
  Starry（中国） Ye Lizhi — 入队 2024-12-26（23 个月）
  C4LLM3SU3（中国） Su Qihao — 入队 2025-02-21（21 个月）
  教练 GUM（中国） Dai Yifei — 入队 2023-03-??
  · 磨合：五人里最久 9.5 年，最近加入 21 个月，平均 4.9 年
```

---

## 7. `cs_gap_talk` 图间素材

```
- 刚那张 Mirage 是 13:11，咬得很紧 —— 可以问她哪一局最揪心，别自己编局面细节
- 卡池这张：省一口气的经济局不是放弃，是攒下一把的枪。可以问她：你受得了队友硬 eco 吗
- 可以翻旧账：你自己刚说过「这波 he 得背锅」
- 规矩：只准用上面出现的数字和比分；别编选手数据、别编战术细节、别说成解说。
```

最后那条约束是写死在返回值里的，每轮都会跟着进上下文。
