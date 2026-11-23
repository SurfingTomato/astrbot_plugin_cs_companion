# 由 tools/make_cores.py 从 plugins/astrbot_plugin_cs_review/main.py 生成。
# 这里**不要**加 @register / @llm_tool / @filter.command：
# 子模块的 handler 不会被 AstrBot 绑定到插件实例（按模块路径精确匹配），
# 装饰器统一留在 main.py 的转发壳上。
"""复盘台：赛后逐人数据 + 有据可依的"谁在犯罪"。

数据来自 5Eplay 的 `session_info`（注意是 session_info，不是 session_list）。
每张图每队各 5 条 pr_stats，字段真实存在且齐全：
kill/death/assist、adr、kast、rating、kd_diff、fkdiff（首杀差）、
first_blood_num / first_death_num、k2~k5（多杀）、round_mvp、swing（回合影响力）、
traded_death、cl_win_num（残局）。

定罪规则是写死的、会打印出来的，不是随口说：
- 扛把子：赢方 rating >= 1.25，或 rating >= 1.10 且首杀差 >= +3
- 悲情英雄：输方 rating >= 1.15 且 KD 为正 —— 数据好但队伍输了
- 背锅位：rating <= 0.85，或（首杀差 <= -3 且 KAST <= 65%）—— 送了开局又没参与回合
- 隐身：KAST <= 60% 且 rating 在 0.85~1.05 之间 —— 没崩，但也没作用
其余算"中规中矩"。

不做的：不给"心理素质""战术执行"这类没有数据的判断，也不评价选手人品。
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context

LIST_API = "https://app.5eplay.com/api/tournament/session_list"
INFO_API = "https://app.5eplay.com/api/tournament/session_info"
REFERER = "https://event.5eplay.com/csgo/matches"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
TZ = timezone(timedelta(hours=8))
TIMEOUT = 15
LIST_TTL = 180
INFO_TTL = 3600  # 已结束的比赛数据不变，缓存一小时足够

RULES = (
    "判据：赢方 rating>=1.25（或 >=1.10 且首杀差>=+3）→ 扛把子；"
    "输方 rating>=1.15 且（KD>0 或 rating>=1.25）→ 悲情英雄；"
    "rating<=0.85，或首杀差<=-3 且 KAST<=65% → 背锅位；"
    "KAST<=60% 且 rating 0.85~1.05 → 隐身；其余中规中矩。"
)


def ts2str(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(int(ts), TZ).strftime("%m-%d %H:%M")
    except Exception:
        return ""


def f(v: Any, default: float = 0.0) -> float:
    try:
        s = str(v).strip().replace("%", "").replace("+", "")
        return float(s) if s else default
    except Exception:
        return default


def pct(v: Any) -> str:
    return f"{f(v):.0f}%"


class CsReview:
    def __init__(self, context: Context, config: dict | None = None):
        self.context = context
        self.config = dict(config or {})
        self._session: aiohttp.ClientSession | None = None
        self._list_cache: tuple[float, list[dict[str, Any]]] | None = None
        self._info_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    # ---------- 网络 ----------
    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=TIMEOUT),
                headers={"User-Agent": UA, "Referer": REFERER},
            )
        return self._session

    async def _get(self, url: str, params: dict[str, Any]) -> dict[str, Any] | None:
        try:
            sess = await self._sess()
            async with sess.get(url, params=params) as resp:
                if resp.status != 200:
                    logger.warning(f"[cs_review] HTTP {resp.status}")
                    return None
                return await resp.json(content_type=None)
        except Exception as exc:
            logger.warning(f"[cs_review] 请求失败: {type(exc).__name__} {exc}")
            return None

    async def _finished(self, pages: int = 2) -> list[dict[str, Any]]:
        """已结束的比赛，最近的在前。空结果会重试一次（5E 偶尔限流回空）。"""
        if self._list_cache and time.time() - self._list_cache[0] < LIST_TTL:
            return self._list_cache[1]
        rows: list[dict[str, Any]] = []
        for attempt in (1, 2):
            out: dict[str, dict[str, Any]] = {}
            for page in range(1, max(1, pages) + 1):
                data = await self._get(
                    LIST_API,
                    {"game_status": 2, "game_type": 1, "page": page, "limit": 100},
                )
                page_rows = ((data or {}).get("data") or {}).get("matches") or []
                for r in page_rows:
                    mid = ((r.get("mc_info") or {}).get("id")) or ""
                    if mid:
                        out[mid] = r
                if not page_rows:
                    break
            rows = sorted(
                out.values(),
                key=lambda r: int((r.get("mc_info") or {}).get("plan_ts") or 0),
                reverse=True,
            )
            if rows:
                break
            await asyncio.sleep(1.2)
        if rows:
            self._list_cache = (time.time(), rows)
        return rows

    async def _info(self, match_id: str) -> dict[str, Any] | None:
        hit = self._info_cache.get(match_id)
        if hit and time.time() - hit[0] < INFO_TTL:
            return hit[1]
        data = await self._get(INFO_API, {"match_id": match_id, "id": match_id})
        body = ((data or {}).get("data") or {}).get("match")
        if not body:
            return None
        self._info_cache[match_id] = (time.time(), body)
        return body

    # ---------- 找比赛 ----------
    async def _pick(self, team: str, scan: int = 8) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None:
        """找一场**真有逐人数据**的已结束比赛。

        刚打完的比赛 session_info 里 pr_stats 是空的（要等几分钟才落库），
        所以不能拿到第一场就用，得往后翻几场。
        """
        best: tuple | None = None
        for row in await self._finished(3):
            info = row.get("mc_info") or {}
            t1 = (info.get("t1_info") or {}).get("disp_name") or ""
            t2 = (info.get("t2_info") or {}).get("disp_name") or ""
            if team and team.lower() not in t1.lower() and team.lower() not in t2.lower():
                continue
            body = await self._info(info.get("id") or "")
            if not body:
                continue
            has = any(
                (b.get("t1_pr_stats") or b.get("t2_pr_stats"))
                for b in (body.get("bouts_state") or [])
            )
            if has:
                return row, info, body
            if best is None:
                best = (row, info, body)
            scan -= 1
            if scan <= 0:
                break
        return best

    # ---------- 解析 ----------
    def _series(self, body: dict[str, Any]) -> dict[str, Any]:
        """把一场 BO3 折叠成「每队每人一行」的系列赛合计。"""
        bouts = [
            b for b in (body.get("bouts_state") or [])
            if b.get("map_name") and str(b.get("map_name")).upper() not in ("TBA", "DEFAULT")
            and b.get("t1_stats", {}).get("all_score") not in ("", None)
        ]
        maps = []
        teams: dict[str, dict[str, dict[str, Any]]] = {"t1": {}, "t2": {}}

        for b in bouts:
            s1 = f(b.get("t1_stats", {}).get("all_score"))
            s2 = f(b.get("t2_stats", {}).get("all_score"))
            rounds = max(1.0, s1 + s2)
            maps.append({"name": b.get("map_name"), "s1": int(s1), "s2": int(s2), "rounds": rounds})
            for side in ("t1", "t2"):
                for p in (b.get(f"{side}_pr_stats") or []):
                    key = p.get("id") or p.get("name") or "?"
                    cur = teams[side].setdefault(
                        key,
                        {"name": p.get("name") or key, "kill": 0, "death": 0, "assist": 0,
                         "adr_w": 0.0, "rounds": 0.0, "kast_w": 0.0, "rating_w": 0.0,
                         "hs": 0, "fk": 0, "fd": 0, "kd": 0, "mvp": 0, "k3": 0, "k4": 0, "k5": 0,
                         "swing": 0.0, "maps": 0},
                    )
                    cur["kill"] += int(f(p.get("kill")))
                    cur["death"] += int(f(p.get("death")))
                    cur["assist"] += int(f(p.get("assist")))
                    cur["hs"] += int(f(p.get("headshot")))
                    cur["fk"] += int(f(p.get("first_blood_num")))
                    cur["fd"] += int(f(p.get("first_death_num")))
                    cur["mvp"] += int(f(p.get("round_mvp")))
                    for k in ("k3", "k4", "k5"):
                        cur[k] += int(f(p.get(k)))
                    cur["swing"] += f(p.get("swing"))
                    cur["adr_w"] += f(p.get("adr")) * rounds
                    cur["kast_w"] += f(p.get("kast")) * rounds
                    cur["rating_w"] += f(p.get("rating")) * rounds
                    cur["rounds"] += rounds
                    cur["maps"] += 1

        for side in ("t1", "t2"):
            for cur in teams[side].values():
                r = cur["rounds"] or 1
                cur["adr"] = cur["adr_w"] / r
                cur["kast"] = cur["kast_w"] / r
                cur["rating"] = cur["rating_w"] / r
                cur["kd"] = cur["kill"] - cur["death"]
                cur["fkdiff"] = cur["fk"] - cur["fd"]
                cur["kd_rate"] = cur["kill"] / cur["death"] if cur["death"] else float(cur["kill"])
        return {"maps": maps, "t1": list(teams["t1"].values()), "t2": list(teams["t2"].values())}

    def _verdict(self, p: dict[str, Any], won: bool) -> str:
        rate, kd, fkd, kast = p["rating"], p["kd"], p["fkdiff"], p["kast"]
        if won:
            if rate >= 1.25 or (rate >= 1.10 and fkd >= 3):
                return "扛把子"
            if kast <= 60 and 0.85 <= rate <= 1.05:
                return "隐身"
            if rate <= 0.85:
                return "混赢"
            return "中规中矩"
        if rate >= 1.15 and (kd > 0 or rate >= 1.25):
            return "悲情英雄"
        if rate <= 0.85 or (fkd <= -3 and kast <= 65):
            return "背锅位"
        if kast <= 60 and 0.85 <= rate <= 1.05:
            return "隐身"
        return "中规中矩"

    @staticmethod
    def _width(groups: list[list[dict[str, Any]]]) -> int:
        n = max((len(p["name"]) for g in groups for p in g), default=8)
        return min(16, max(12, n + 1))

    @staticmethod
    def _row(p: dict[str, Any], verdict: str, width: int) -> str:
        star = "★" if verdict in ("扛把子", "悲情英雄") else " "
        return (
            f"  {star}{p['name']:<{width}}{p['kill']:>2}-{p['death']:>2}-{p['assist']:<3}"
            f" ADR {p['adr']:5.1f}  KAST {p['kast']:4.0f}%  "
            f"KD {p['kd']:+d}  首杀差 {p['fkdiff']:+d}  评分 {p['rating']:.2f}  {verdict}"
        )

    def _render(self, body: dict[str, Any], info: dict[str, Any], series: dict[str, Any]) -> str:
        t1 = (info.get("t1_info") or {}).get("disp_name") or "T1"
        t2 = (info.get("t2_info") or {}).get("disp_name") or "T2"
        bo = {"1": "BO1", "2": "BO2", "3": "BO3", "5": "BO5"}.get(str(info.get("format")), "")
        stage = info.get("round_name") or (info.get("tt_info") or {}).get("disp_name") or ""
        maps = series["maps"]
        w1 = sum(1 for m in maps if m["s1"] > m["s2"])
        w2 = len(maps) - w1

        lines = [
            f"【赛后复盘】{t1} {w1}-{w2} {t2}　{bo}　{ts2str(info.get('plan_ts'))}　{stage}".rstrip()
        ]
        lines.append(
            "逐图：" + "｜".join(f"{m['name']} {m['s1']}:{m['s2']}" for m in maps)
        )

        width = self._width([series["t1"], series["t2"]])
        for side, name, won, maps_won in (
            ("t1", t1, w1 > w2, w1),
            ("t2", t2, w2 > w1, w2),
        ):
            plist = sorted(series[side], key=lambda x: -x["rating"])
            if not plist:
                lines.append(f"\n—— {name}（无个人数据）——")
                continue
            verdicts = {id(p): self._verdict(p, won) for p in plist}
            # 背锅位只点一个人：一堆人并列时，留评分最低的那个，其余记"低迷"
            if not won:
                cands = [p for p in plist if verdicts[id(p)] == "背锅位"]
                if len(cands) > 1:
                    worst = min(cands, key=lambda x: x["rating"])
                    for p in cands:
                        if p is not worst:
                            verdicts[id(p)] = "低迷"
                elif not cands and plist[-1]["rating"] <= 0.95:
                    verdicts[id(plist[-1])] = "背锅位"

            lines.append(f"\n—— {name}（{'赢' if won else '输'}，图分 {maps_won}）——")
            for p in plist:
                lines.append(self._row(p, verdicts[id(p)], width))

            if won:
                best = plist[0]
                if best["rating"] >= 1.25:
                    lines.append(f"  → 这场是 {best['name']} 在扛（评分 {best['rating']:.2f}）")
            else:
                hero = [p for p in plist if verdicts[id(p)] == "悲情英雄"]
                if hero:
                    h = hero[0]
                    lines.append(
                        f"  → 悲情英雄是 {h['name']}：{h['kill']}-{h['death']}、ADR {h['adr']:.0f}、"
                        f"评分 {h['rating']:.2f}，数据够了但队伍没赢下来"
                    )
                blame = [p for p in plist if verdicts[id(p)] == "背锅位"]
                if blame:
                    b = blame[0]
                    why = []
                    if b["rating"] <= 0.85:
                        why.append(f"评分只有 {b['rating']:.2f}")
                    if b["fkdiff"] <= -3:
                        why.append(f"首杀差 {b['fkdiff']:+d}（开局先送）")
                    if b["kast"] <= 65:
                        why.append(f"KAST {b['kast']:.0f}%（回合参与不够）")
                    lines.append(
                        f"  → 背锅位是 {b['name']}：{b['kill']}-{b['death']}"
                        + (f"，{'、'.join(why)}" if why else "")
                    )
                else:
                    lines.append("  → 没有人该单独背锅，是整队的问题（五人评分都在合理区间）")

        lines.append("\n" + RULES)
        lines.append(
            "（数据来自 5Eplay 官方赛事数据，评分是 5E 自己的算法，不是 HLTV Rating。"
            "只评价这张纸上有的东西，别给人加心理戏。）"
        )
        return "\n".join(lines)

    # ---------- 工具 1：复盘 ----------
    async def cs_review(self, event: AstrMessageEvent, team: str = "") -> str:
        """赛后复盘：逐人 K-D-A / ADR / KAST / 评分，并判定谁扛、谁是悲情英雄、谁背锅。

        比赛打完后用。不传队名就取最近结束的一场。

        Args:
            team(string): 队名关键词，留空取最近一场已结束的比赛
        """
        picked = await self._pick((team or "").strip())
        if not picked:
            return f"没找到「{team}」已结束的比赛。" if team else "最近没有已结束的比赛。"
        row, info, body = picked
        mid = info.get("id") or ""

        series = self._series(body)
        if not series["t1"] and not series["t2"]:
            return (
                f"{mid} 这场还没有逐人数据（刚打完的比赛通常要等几分钟），"
                f"别凭比分猜谁打得好。"
            )
        merged = dict(info)
        merged["tt_info"] = row.get("tt_info") or {}
        merged["round_name"] = info.get("round_name") or merged["tt_info"].get("disp_name") or ""
        return self._render(body, merged, series)

    # ---------- 工具 2：纯数据 ----------
    async def cs_match_stats(self, event: AstrMessageEvent, team: str = "") -> str:
        """只要逐人数据表，不下判断。想自己解读或只查某个人的数据时用。

        Args:
            team(string): 队名关键词，留空取最近一场已结束的比赛
        """
        picked = await self._pick((team or "").strip())
        if not picked:
            return f"没找到「{team}」已结束的比赛。" if team else "最近没有已结束的比赛。"
        row, info, body = picked
        series = self._series(body)
        t1 = (info.get("t1_info") or {}).get("disp_name") or "T1"
        t2 = (info.get("t2_info") or {}).get("disp_name") or "T2"
        w = self._width([series["t1"], series["t2"]])
        lines = [
            f"【逐人数据】{t1} vs {t2}　{ts2str(info.get('plan_ts'))}",
            "逐图：" + "｜".join(f"{m['name']} {m['s1']}:{m['s2']}" for m in series["maps"]),
            f"\n{'选手':<{w}}{'K':>3}{'D':>4}{'A':>4}{'ADR':>7}{'KAST':>7}{'KD差':>6}{'首杀':>5}{'多杀':>5}{'MVP':>5}{'评分':>6}",
        ]
        for side, name in (("t1", t1), ("t2", t2)):
            lines.append(f"— {name} —")
            for p in sorted(series[side], key=lambda x: -x["rating"]):
                multi = p["k3"] + p["k4"] + p["k5"]
                lines.append(
                    f"{p['name']:<{w}}{p['kill']:>3}{p['death']:>4}{p['assist']:>4}"
                    f"{p['adr']:>7.1f}{p['kast']:>6.0f}%{p['kd']:>+6d}{p['fk']:>5}{multi:>5}"
                    f"{p['mvp']:>5}{p['rating']:>6.2f}"
                )
        lines.append("\n（多杀 = 3 杀及以上回合数；评分是 5E 算法。没有数据就不写。）")
        return "\n".join(lines)

    async def review_cmd(self, event: AstrMessageEvent, arg: str = ""):
        """快捷复盘：/review 或 /review FaZe"""
        try:
            yield event.plain_result(await self.cs_review(event, arg.strip()))
        except Exception as exc:
            yield event.plain_result(f"复盘失败：{type(exc).__name__}")

    async def terminate(self):
        if self._session and not self._session.closed:
            await self._session.close()
