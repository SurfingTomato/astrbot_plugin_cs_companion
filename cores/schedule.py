# 由 tools/make_cores.py 从 plugins/astrbot_plugin_cs_schedule/main.py 生成。
# 这里**不要**加 @register / @llm_tool / @filter.command：
# 子模块的 handler 不会被 AstrBot 绑定到插件实例（按模块路径精确匹配），
# 装饰器统一留在 main.py 的转发壳上。
"""赛程簿：往前看、往后看、以及"下一轮大概会撞谁"。

三个能力：
1. 未来赛程 —— status=0 分页能拿到整届 Major 的预排场次（包含后续轮次的 TBD 占位）
2. 历史赛程与统计 —— status=2 分页，每场带逐图比分、半场比分、击杀数，
   所以能算出真实的地图胜率、回合差、场均击杀，而不是只看大分
3. 瑞士轮推演 —— 按 3 胜晋级 / 3 负淘汰 + 同战绩组内高 Buchholz 打低 Buchholz 的规则，
   用已完成的比赛推当前积分榜与下一轮可能的配对

诚实边界：瑞士轮配对是**推算**（5Eplay 只给 TBD 占位，不会提前公布配对），
胜率也是估算。工具返回值里都写清楚了，别当官宣。
"""

from __future__ import annotations

import asyncio
import math
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent
from astrbot.api.star import Context

API_URL = "https://app.5eplay.com/api/tournament/session_list"
REFERER = "https://event.5eplay.com/csgo/matches"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
TZ = timezone(timedelta(hours=8))
TIMEOUT = 15
UPCOMING_TTL = 120
HISTORY_TTL = 900
MAX_HISTORY_PAGES = 10


class FiveE:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._pages: dict[str, tuple[float, list[dict[str, Any]]]] = {}

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": UA, "Referer": REFERER, "Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=TIMEOUT),
            )
        return self._session

    async def page(self, status: int, page: int = 1, limit: int = 100) -> list[dict[str, Any]]:
        key = f"{status}:{page}:{limit}"
        hit = self._pages.get(key)
        ttl = UPCOMING_TTL if status in (0, 1) else HISTORY_TTL
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
        sess = await self._sess()
        params = {
            "game_status": str(status),
            "game_type": "1",
            "page": str(page),
            "limit": str(limit),
        }
        async with sess.get(API_URL, params=params) as resp:
            if resp.status != 200:
                raise RuntimeError(f"5Eplay 返回 {resp.status}")
            payload = await resp.json(content_type=None)
        rows = (payload.get("data") or {}).get("matches") or []
        self._pages[key] = (time.time(), rows)
        return rows

    async def upcoming(self) -> list[dict[str, Any]]:
        """今日 + 未来。status=0 的第 1、2 页合起来能盖住整届赛事的预排场次。"""
        rows: list[dict[str, Any]] = []
        for st in (1, 0):
            for p in ((1,) if st == 1 else (1, 2)):
                try:
                    rows.extend(await self.page(st, p))
                except Exception:
                    continue
        seen: set[str] = set()
        out = []
        for r in rows:
            mid = (r.get("mc_info") or {}).get("id")
            if mid and mid not in seen:
                seen.add(mid)
                out.append(r)
        return out

    async def history(self, days: int = 12) -> list[dict[str, Any]]:
        cutoff = time.time() - days * 86400
        out: list[dict[str, Any]] = []
        for p in range(1, MAX_HISTORY_PAGES + 1):
            try:
                rows = await self.page(2, p)
            except Exception:
                break
            if not rows:
                break
            out.extend(rows)
            ts = [int((r.get("mc_info") or {}).get("plan_ts") or 0) for r in rows]
            if ts and max(t for t in ts if t) < cutoff:
                break
        return out

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


def norm(raw: dict[str, Any]) -> dict[str, Any]:
    i = raw.get("mc_info") or {}
    s = raw.get("state") or {}
    tt = raw.get("tt_info") or {}
    bouts = [b for b in (s.get("bout_states") or []) if b.get("map_name")]

    maps = []
    for b in bouts:
        try:
            a = int(b.get("t1_score") or 0)
            c = int(b.get("t2_score") or 0)
        except (TypeError, ValueError):
            a = c = 0
        maps.append(
            {
                "n": b.get("bout_num") or len(maps) + 1,
                "name": b.get("map_name"),
                "s1": a,
                "s2": c,
                "done": str(b.get("status") or "") == "2",
                "k1": int(b.get("t1_kill") or 0),
                "k2": int(b.get("t2_kill") or 0),
                "ot": bool(b.get("t1_ot_rounds") or b.get("t2_ot_rounds")),
            }
        )

    try:
        s1 = int(s.get("t1_score") or 0)
        s2 = int(s.get("t2_score") or 0)
    except (TypeError, ValueError):
        s1 = s2 = 0
    s1 = max(s1, sum(1 for b in bouts if str(b.get("result") or "") == "t1"))
    s2 = max(s2, sum(1 for b in bouts if str(b.get("result") or "") == "t2"))

    def _pct(v: Any) -> int | None:
        try:
            n = int(v)
        except (TypeError, ValueError):
            return None
        return n or None

    return {
        "id": i.get("id") or "",
        "tt": tt.get("id") or "",
        "event": tt.get("disp_name") or "",
        "grade": tt.get("special_grade_label") or tt.get("grade_label") or "",
        "stage": i.get("tt_stage_desc") or i.get("round_name") or i.get("tt_stage") or "",
        "round": i.get("round_name") or "",
        "plan_ts": int(i.get("plan_ts") or 0),
        "t1": ((i.get("t1_info") or {}).get("disp_name") or "TBD").strip(),
        "t2": ((i.get("t2_info") or {}).get("disp_name") or "TBD").strip(),
        "bo": str(i.get("format") or "1"),
        "status": str(s.get("status") or "0"),
        "s1": s1,
        "s2": s2,
        "maps": maps,
        "o1": _pct(s.get("t1_odds_percent")),
        "o2": _pct(s.get("t2_odds_percent")),
        "tags": (i.get("tags") or "").strip(),
    }


def tbd(m: dict[str, Any]) -> bool:
    return m["t1"] in ("TBD", "") or m["t2"] in ("TBD", "")


def hhmm(ts: int) -> str:
    return datetime.fromtimestamp(ts, TZ).strftime("%m-%d %H:%M") if ts else "时间未定"


def is_win(m: dict[str, Any], team: str) -> bool:
    low = team.lower()
    if m["t1"].lower() == low:
        return m["s1"] > m["s2"]
    return m["s2"] > m["s1"]


def opponent(m: dict[str, Any], team: str) -> str:
    return m["t2"] if m["t1"].lower() == team.lower() else m["t1"]


class TeamStats:
    __slots__ = ("team", "series", "wins", "losses", "maps_w", "maps_l", "rounds_for",
                 "rounds_against", "kills_for", "kills_against", "ot", "last")

    def __init__(self, team: str) -> None:
        self.team = team
        self.series = self.wins = self.losses = 0
        self.maps_w = self.maps_l = 0
        self.rounds_for = self.rounds_against = 0
        self.kills_for = self.kills_against = 0
        self.ot = 0
        self.last: list[str] = []

    def add(self, m: dict[str, Any]) -> None:
        self.series += 1
        won = is_win(m, self.team)
        self.wins += 1 if won else 0
        self.losses += 0 if won else 1
        me_first = m["t1"].lower() == self.team.lower()
        for mp in m["maps"]:
            if not mp["done"]:
                continue
            a, b = (mp["s1"], mp["s2"]) if me_first else (mp["s2"], mp["s1"])
            self.maps_w += 1 if a > b else 0
            self.maps_l += 0 if a > b else 1
            self.rounds_for += a
            self.rounds_against += b
            ka, kb = (mp["k1"], mp["k2"]) if me_first else (mp["k2"], mp["k1"])
            self.kills_for += ka
            self.kills_against += kb
            if mp["ot"]:
                self.ot += 1
        day = datetime.fromtimestamp(m["plan_ts"], TZ).strftime("%m-%d")
        a, b = (m["s1"], m["s2"]) if me_first else (m["s2"], m["s1"])
        self.last.append(f"{day} {'胜' if won else '负'} {opponent(m, self.team)} {a}:{b}")

    def report(self) -> str:
        maps = self.maps_w + self.maps_l
        map_rate = f"{self.maps_w}/{maps}" if maps else "0/0"
        rd = self.rounds_for - self.rounds_against
        kd = (self.kills_for / self.kills_against) if self.kills_against else 0
        return (
            f"{self.team}：近 {self.series} 场 {self.wins} 胜 {self.losses} 负；"
            f"地图 {map_rate}；回合差 {rd:+d}；场均击杀/被杀 {self.kills_for / max(1, maps):.0f}/"
            f"{self.kills_against / max(1, maps):.0f}（KD {kd:.2f}）"
            + (f"；加时 {self.ot} 次" if self.ot else "")
        )


# ---------------------------------------------------------------- 瑞士轮


def swiss_table(matches: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """从已完成的比赛算战绩 + Buchholz。只统计真正打完的。"""
    rows: dict[str, dict[str, Any]] = {}
    done = [m for m in matches if m["status"] == "2" and not tbd(m)]
    for m in done:
        for team in (m["t1"], m["t2"]):
            st = rows.setdefault(team, {"team": team, "w": 0, "l": 0, "opps": [], "results": []})
            won = is_win(m, team)
            st["w"] += 1 if won else 0
            st["l"] += 0 if won else 1
            st["opps"].append(opponent(m, team))
            st["results"].append((opponent(m, team), won))
    for team, st in rows.items():
        st["buchholz"] = sum(rows.get(o, {}).get("w", 0) for o in st["opps"])
        st["played"] = {o for o in st["opps"]}
    return rows


def swiss_predict(rows: dict[str, dict[str, Any]], wins_needed: int = 3) -> list[tuple[str, str]]:
    """同战绩组内按 Buchholz 高打低。简单可靠，不追求和官方逐场一致。"""
    groups: dict[int, list[dict[str, Any]]] = {}
    for st in rows.values():
        if st["w"] >= wins_needed or st["l"] >= wins_needed:
            continue
        groups.setdefault(st["w"], []).append(st)

    pairs: list[tuple[str, str]] = []
    carry: list[dict[str, Any]] = []
    for w in sorted(groups.keys(), reverse=True):
        pool = carry + groups[w]
        carry = []
        pool = [p for p in pool if p["w"] == w] + [p for p in pool if p["w"] != w]
        pool.sort(key=lambda s: -s.get("buchholz", 0))
        while len(pool) >= 2:
            a = pool.pop(0)
            pick = None
            for idx, b in enumerate(pool):
                if b["team"] not in a["played"]:
                    pick = pool.pop(idx)
                    break
            if pick is None:
                pick = pool.pop(0)
            pairs.append((a["team"], pick["team"]))
        if pool:
            carry = pool
    return pairs


class CsSchedule:
    def __init__(self, context: Context, config: dict | None = None):
        self.context = context
        self.config = dict(config or {})
        self._fe = FiveE()

    # ---------- 内部 ----------

    def _pick_event(self, rows: list[dict[str, Any]], event: str) -> tuple[str, str]:
        """返回 (赛事名, tt id)。默认取最近的 Major。"""
        q = (event or "").strip().lower()
        cands: dict[str, tuple[str, int]] = {}
        for m in rows:
            key = m["event"] or ""
            if not key:
                continue
            if q and q not in key.lower():
                continue
            if not q and "major" not in key.lower():
                continue
            item = cands.setdefault(key, [m["tt"], 0])
            item[1] += 1
        if not cands:
            return "", ""
        best = max(cands.items(), key=lambda kv: kv[1][1])[0]
        return best, cands[best][0]

    async def _event_rows(self, event: str, days: int = 20) -> tuple[str, list[dict[str, Any]]]:
        up = [norm(r) for r in await self._fe.upcoming()]
        name, tt = self._pick_event(up, event)
        pool = list(up)
        if not name:
            hist = [norm(r) for r in await self._fe.history(days)]
            name, tt = self._pick_event(hist, event)
            pool.extend(hist)
        if not name:
            return "", []
        return name, [m for m in pool if m["event"] == name]

    # ---------- 工具 ----------

    async def cs_schedule(self, event: AstrMessageEvent, days: int = 3, team: str = "", event_name: str = "") -> str:
        """看未来赛程（今天起若干天内），含未定对阵的占位场次。

        用户问"明天几点开始""接下来有什么""这届赛程怎么排"时调用。
        瑞士轮后续轮次在赛前只显示 TBD vs TBD，这是正常的占位，不要说成大问题。

        Args:
            days(number): 看几天，默认 3，最大 14
            team(string): 只看某队，可留空
            event_name(string): 赛事名关键词，默认自动找最近的 Major
        """
        return await self._schedule_text(days, team, event_name)

    async def _schedule_text(self, days: int = 3, team: str = "", event_name: str = "") -> str:
        days = max(1, min(int(days or 3), 14))
        try:
            rows = [norm(r) for r in await self._fe.upcoming()]
        except Exception as exc:
            return f"赛程接口没返回（{type(exc).__name__}），老实说查不到。"
        if not rows:
            return "赛程接口是空的，直接说查不到。"

        name, _ = self._pick_event(rows, event_name)
        if event_name:
            rows = [m for m in rows if name and m["event"] == name]
        else:
            # 没指定就整届一起看（第1阶段/第2阶段是两场赛事，但用户眼里就是同一个 Major）
            rows = [m for m in rows if "major" in m["event"].lower()]
            name = "PGL 新加坡 Major 2026" if rows and "新加坡" in rows[0]["event"] else name
        now = time.time()
        end = now + days * 86400
        rows = [m for m in rows if now - 6 * 3600 <= m["plan_ts"] <= end]
        if team:
            low = team.strip().lower()
            rows = [m for m in rows if low in m["t1"].lower() or low in m["t2"].lower()]
        if not rows:
            return f"未来 {days} 天没查到符合条件的场次。直接说没有，别编。"

        rows.sort(key=lambda m: m["plan_ts"])
        multi = len({m["event"] for m in rows}) > 1
        by_day: dict[str, list[str]] = {}
        for m in rows:
            day = hhmm(m["plan_ts"])[:5]
            tag = ""
            if multi:
                # 同一届赛事的两个阶段名字不一样，单看"第一轮"会分不清是哪一阶段
                tag = "［" + m["event"].replace("PGL 新加坡 Major 2026", "").strip() + "］"
            if tbd(m):
                line = f"　{hhmm(m['plan_ts'])[6:]} {m['stage'] or m['round']}　对阵未定（TBD）{tag}"
            else:
                odds = f"　{m['o1']}%/{m['o2']}%" if m["o1"] and m["o2"] else ""
                extra = f"　[{m['tags']}]" if m["tags"] else ""
                line = (
                    f"　{hhmm(m['plan_ts'])[6:]} {m['t1']} vs {m['t2']}"
                    f"　BO{m['bo']}　{m['stage'] or m['round']}{odds}{extra}{tag}"
                )
            by_day.setdefault(day, []).append(line)

        out = [f"【{name}·未来 {days} 天】"]
        for day in sorted(by_day):
            out.append(day)
            out.extend(by_day[day][:14])
            if len(by_day[day]) > 14:
                out.append(f"　…还有 {len(by_day[day]) - 14} 场")
        out.append("（TBD 是赛前占位，等上一轮打完才会填队名。别把 TBD 说成队伍。）")
        return "\n".join(out)

    async def cs_history(self, event: AstrMessageEvent, team: str, days: int = 12, limit: int = 8) -> str:
        """查某队最近的历史赛果与真实统计（地图胜率、回合差、场均击杀、KD、加时次数）。

        用户问"他们最近状态怎么样""赢了几场""对上谁吃亏"时调用。
        统计只用打完的比赛算，没打完的不会混进去。

        Args:
            team(string): 队名，例如 "FaZe"、"Liquid"
            days(number): 往前查几天，默认 12，最大 30（查得越久越慢）
            limit(number): 最多列几场赛果，默认 8
        """
        team = (team or "").strip()
        if not team:
            return "没给队名。问用户要看哪支队。"
        days = max(1, min(int(days or 12), 30))
        limit = max(1, min(int(limit or 8), 15))
        try:
            rows = [norm(r) for r in await self._fe.history(days)]
        except Exception as exc:
            return f"历史接口没返回（{type(exc).__name__}）。查不到就说查不到，别拿记忆里的战绩顶。"
        low = team.lower()
        mine = [
            m for m in rows
            if m["status"] == "2" and not tbd(m) and (m["t1"].lower() == low or m["t2"].lower() == low)
        ]
        if not mine:
            return f"近 {days} 天没查到 {team} 打完的比赛。如实说没有，别编。"

        mine.sort(key=lambda m: m["plan_ts"])
        st = TeamStats(team)
        for m in mine:
            st.add(m)
        out = [st.report(), "最近几场："]
        out.extend(f"· {x}" for x in st.last[-limit:])
        # 对手画像：怕谁、吃谁
        by_opp: dict[str, list[bool]] = {}
        for m in mine:
            by_opp.setdefault(opponent(m, team), []).append(is_win(m, team))
        hard = [(o, r) for o, r in by_opp.items() if len(r) >= 2 and sum(r) <= len(r) / 2]
        if hard:
            out.append("交手两次以上没占到便宜的：" + "、".join(f"{o}（{sum(r)}胜{len(r) - sum(r)}负）" for o, r in hard[:4]))
        out.append("（这些数字都是真的；别说成'数据帝'，挑一两条用就行。）")
        return "\n".join(out)

    async def cs_swiss(self, event: AstrMessageEvent, event_name: str = "") -> str:
        """推演瑞士轮：当前积分榜 + Buchholz + 下一轮可能的配对。

        用户问"现在谁出线了""下一轮打谁""瑞士轮怎么算"时调用。
        配对是**按规则推算的**，官方赛前只放 TBD 占位，所以必须说明这是推算。

        Args:
            event_name(string): 赛事名关键词，默认最近的 Major
        """
        try:
            name, rows = await self._event_rows(event_name)
        except Exception as exc:
            return f"接口没返回（{type(exc).__name__}），推不了。"
        if not name:
            return "没找到对应的赛事。直接说没有。"

        table = swiss_table(rows)
        if not table:
            starters = sorted({m["t1"] for m in rows if not tbd(m)} | {m["t2"] for m in rows if not tbd(m)})
            if starters:
                return (
                    f"【{name}】还没有打完的比赛，积分榜是空的。"
                    f"已知参赛队：{'、'.join(starters[:18])}。"
                    "等第一轮出结果再推，现在推算等于瞎猜。"
                )
            return f"【{name}】查不到已完成场次，也查不到参赛队，推不了。"

        alive = [s for s in table.values() if s["w"] < 3 and s["l"] < 3]
        alive.sort(key=lambda s: (-s["w"], s["l"], -s.get("buchholz", 0)))
        out = [f"【{name}·瑞士轮推演】已完成 {sum(1 for m in rows if m['status'] == '2')} 场"]
        out.append("战绩 / Buchholz：")
        for s in alive[:18]:
            out.append(f"· {s['team']}　{s['w']}-{s['l']}　BH {s.get('buchholz', 0)}")
        advanced = [s for s in table.values() if s["w"] >= 3]
        outdrawn = [s for s in table.values() if s["l"] >= 3]
        if advanced:
            out.append("已 3 胜：" + "、".join(s["team"] for s in advanced))
        if outdrawn:
            out.append("已 3 负出局：" + "、".join(s["team"] for s in outdrawn))

        pairs = swiss_predict(table)
        if pairs:
            out.append("下一轮可能这么配（推算，非官宣）：")
            for a, b in pairs[:10]:
                sa, sb = table.get(a, {}), table.get(b, {})
                out.append(
                    f"· {a}（{sa.get('w', 0)}-{sa.get('l', 0)}）vs {b}（{sb.get('w', 0)}-{sb.get('l', 0)}）"
                )
        out.append("规矩：配对规则是同战绩组内高 Buchholz 打低 Buchholz、尽量不重赛；说的时候要讲明是推算。")
        return "\n".join(out)

    async def cs_predict(self, event: AstrMessageEvent, team_a: str, team_b: str) -> str:
        """估算两队交手胜率，并给出依据（赔率优先，其次近期战绩与回合差）。

        用户问"谁会赢""你押谁"时调用。结果只是估算，必须带上不确定性说。

        Args:
            team_a(string): 一队
            team_b(string): 另一队
        """
        a, b = (team_a or "").strip(), (team_b or "").strip()
        if not a or not b:
            return "要两个队名才能算。"
        try:
            up = [norm(r) for r in await self._fe.upcoming()]
            hist = [norm(r) for r in await self._fe.history(14)]
        except Exception as exc:
            return f"接口没返回（{type(exc).__name__}），算不了。别凭印象押。"

        # 1) 如果这俩正好有排期且带赔率，直接给市场的数
        al, bl = a.lower(), b.lower()
        for m in up:
            names = {m["t1"].lower(), m["t2"].lower()}
            if names == {al, bl} and m["o1"] and m["o2"]:
                pa = m["o1"] if m["t1"].lower() == al else m["o2"]
                return (
                    f"【市场价】{m['t1']} vs {m['t2']}（{hhmm(m['plan_ts'])}，BO{m['bo']}）\n"
                    f"{a} {pa}% / {b} {100 - pa}%\n依据：5Eplay 赛前赔率换算，不是你们算的。"
                    "可以直接引用，但别说得像自己算出来的。"
                )

        # 2) 用近 14 天两队的真实数据估算
        def stats(team: str) -> TeamStats | None:
            low = team.lower()
            mine = [m for m in hist if m["status"] == "2" and not tbd(m)
                    and (m["t1"].lower() == low or m["t2"].lower() == low)]
            if not mine:
                return None
            st = TeamStats(team)
            for m in mine:
                st.add(m)
            return st

        sa, sb = stats(a), stats(b)
        if not sa or not sb:
            missing = a if not sa else b
            return f"近 14 天没有 {missing} 打完的比赛，样本不够，估不出来。直接说没有数据。"

        def strength(s: TeamStats) -> float:
            maps = max(1, s.maps_w + s.maps_l)
            winrate = s.maps_w / maps
            rd = (s.rounds_for - s.rounds_against) / maps
            return winrate * 2.4 + max(-1.2, min(1.2, rd / 6))

        da, db = strength(sa), strength(sb)
        pa = 1 / (1 + math.exp(-(da - db) * 2.2))
        pa_pct = max(15, min(85, round(pa * 100 / 5) * 5))

        out = [f"【估算】{a} {pa_pct}% / {b} {100 - pa_pct}%"]
        out.append(sa.report())
        out.append(sb.report())
        out.append("依据：近 14 天地图胜率 + 回合差（回合差权重压过比分，因为比分会掩盖拉锯）")
        out.append("注意：这是粗估，样本小、没有考虑地图池和阵容变动，别说得像稳赢。")
        return "\n".join(out)

    async def sched_cmd(self, event: AstrMessageEvent, arg: str = ""):
        """快捷看赛程：/sched 或 /sched FaZe"""
        try:
            yield event.plain_result(await self._schedule_text(days=3, team=arg))
        except Exception as exc:
            yield event.plain_result(f"查不到：{type(exc).__name__}")

    async def terminate(self):
        await self._fe.close()
