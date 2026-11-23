"""打赌台：赛前押注，赛后自动结算，输的人欠一件事。

为什么不是简单"你猜谁赢"：
    押注要有代价、要有账本、要能翻旧账，嘴硬才有意思。
    盘口取自 5Eplay 真实赛前赔率与赛制，结算取自真实赛果，她不能赖。

盘口三档（BO1 只有前两档）：
    winner  谁赢
    series  大比分（BO3/BO5）
    ot      这场是否出现加时

计分：每档 1 分。全中 3 分。连胜会被记下来，用来互相挤兑。
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiohttp

from astrbot.api import llm_tool, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

API_URL = "https://app.5eplay.com/api/tournament/session_list"
REFERER = "https://event.5eplay.com/csgo/matches"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
TZ = timezone(timedelta(hours=8))
TIMEOUT = 12
CACHE_TTL = 30


class FiveE:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._cache: dict[int, tuple[float, list[dict[str, Any]]]] = {}

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": UA, "Referer": REFERER, "Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=TIMEOUT),
            )
        return self._session

    async def fetch(self, status: int, limit: int = 60) -> list[dict[str, Any]]:
        hit = self._cache.get(status)
        if hit and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
        sess = await self._sess()
        params = {"game_status": str(status), "game_type": "1", "page": "1", "limit": str(limit)}
        async with sess.get(API_URL, params=params) as resp:
            if resp.status != 200:
                raise RuntimeError(f"5Eplay 返回 {resp.status}")
            payload = await resp.json(content_type=None)
        rows = (payload.get("data") or {}).get("matches") or []
        self._cache[status] = (time.time(), rows)
        return rows

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


def norm(raw: dict[str, Any]) -> dict[str, Any]:
    i = raw.get("mc_info") or {}
    s = raw.get("state") or {}
    tt = raw.get("tt_info") or {}
    bouts = s.get("bout_states") or []
    try:
        s1 = int(s.get("t1_score") or 0)
        s2 = int(s.get("t2_score") or 0)
    except (TypeError, ValueError):
        s1 = s2 = 0
    s1 = max(s1, sum(1 for b in bouts if str(b.get("result") or "") == "t1"))
    s2 = max(s2, sum(1 for b in bouts if str(b.get("result") or "") == "t2"))
    maps = []
    for b in bouts:
        if not b.get("map_name"):
            continue
        maps.append(
            {
                "n": b.get("bout_num") or len(maps) + 1,
                "name": b.get("map_name"),
                "s1": str(b.get("t1_score") or "0"),
                "s2": str(b.get("t2_score") or "0"),
                "done": str(b.get("status") or "") == "2",
            }
        )

    def _pct(v: Any) -> int | None:
        try:
            n = int(v)
        except (TypeError, ValueError):
            return None
        return n or None

    return {
        "id": i.get("id") or "",
        "event": tt.get("disp_name") or "",
        "stage": i.get("round_name") or i.get("tt_stage") or "",
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
    }


def pick(rows: list[dict[str, Any]], query: str) -> dict[str, Any] | None:
    q = (query or "").strip().lower()
    pool = [norm(r) for r in rows]
    if q:
        pool = [
            m
            for m in pool
            if q in " ".join([m["t1"], m["t2"], m["event"], m["stage"]]).lower()
        ]
    if not pool:
        return None
    order = {"1": 0, "0": 1, "2": 2}
    pool.sort(key=lambda m: (order.get(m["status"], 3), m["plan_ts"]))
    return pool[0]


def has_overtime(m: dict[str, Any]) -> bool:
    for mp in m["maps"]:
        try:
            a, b = int(mp["s1"]), int(mp["s2"])
        except ValueError:
            continue
        if max(a, b) >= 16:
            return True
    return False


@register(
    "astrbot_plugin_cs_bet",
    "local",
    "打赌台：赛前押注，赛后自动结算",
    "1.0.0",
)
class CsBet(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = dict(config or {})
        self._fe = FiveE()
        self._path: Path | None = None
        self._state: dict[str, Any] = {
            "bets": [],
            "score": {"her": {"pts": 0, "rounds": 0}, "user": {"pts": 0, "rounds": 0}},
            "streak": {"her": 0, "user": 0},
            "forfeits": [],
        }
        self._load()

    # ---------- 存档 ----------

    def _load(self) -> None:
        try:
            base = Path(StarTools.get_data_dir())
            base.mkdir(parents=True, exist_ok=True)
            self._path = base / "bets.json"
            if self._path.exists():
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._state.update(data)
        except Exception as exc:
            logger.warning(f"[cs_bet] 读取账本失败: {exc}")
            self._path = None

    def _save(self) -> None:
        if not self._path:
            return
        try:
            self._path.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning(f"[cs_bet] 写账本失败: {exc}")

    # ---------- 工具 ----------

    @llm_tool(name="cs_bet_open")
    async def cs_bet_open(self, event: AstrMessageEvent, match: str = "") -> str:
        """开一个盘口：拿真实赛前赔率生成押注题目，等双方各押一注。

        用户说"赌一把"、"押一注"、"我们打个赌"，或者比赛快开始时你想主动开盘，调用本工具。
        开完盘要让用户先押；她自己也押，然后才有意思。

        Args:
            match(string): 队名或赛事名，留空=最近一场未开始的比赛
        """
        try:
            rows = await self._fe.fetch(1) + await self._fe.fetch(0, limit=60)
            m = pick(rows, match)
            if m is None:
                return "赛程里没找到能开盘的比赛。直接说没找到，别编。"
            open_bets = [b for b in self._state["bets"] if b.get("status") == "open"]
            same = [b for b in open_bets if b.get("match_id") == m["id"]]
            if same:
                b = same[0]
                return (
                    f"这盘已经开着：{b['t1']} vs {b['t2']}，"
                    f"她押 {b['picks'].get('her') or '还没押'}，用户押 {b['picks'].get('user') or '还没押'}。"
                )

            bo = m["bo"] or "1"
            markets = ["winner"]
            if bo not in ("1", ""):
                markets.append("series")
            markets.append("ot")
            odds = ""
            if m["o1"] and m["o2"]:
                odds = f"赛前赔率换算胜率 {m['t1']} {m['o1']}% / {m['t2']} {m['o2']}%"

            bet = {
                "id": uuid.uuid4().hex[:10],
                "match_id": m["id"],
                "event": m["event"],
                "stage": m["stage"],
                "t1": m["t1"],
                "t2": m["t2"],
                "bo": bo,
                "markets": markets,
                "opened_ts": time.time(),
                "status": "open",
                "picks": {"her": {}, "user": {}},
            }
            self._state["bets"].append(bet)
            self._state["bets"] = self._state["bets"][-80:]
            self._save()

            lines = [
                f"【开盘】{m['event']} {m['stage']}｜{m['t1']} vs {m['t2']}（BO{bo}）",
            ]
            if odds:
                lines.append(odds)
            marks = "①②③④"
            items = [f"{marks[0]} 谁赢（{m['t1']} / {m['t2']}）"]
            idx = 1
            if "series" in markets:
                need = 2 if bo == "3" else 3
                items.append(f"{marks[idx]} 大比分（{need}:0 / {need}:1 / 1:{need} / 0:{need}）")
                idx += 1
            items.append(f"{marks[idx]} 有没有加时（有 / 没有）")
            lines.append("盘口：" + "；".join(items))
            lines.append("每档 1 分。开完盘先让用户押，你再押；都押完再开打。别把答案说得太准。")
            return "\n".join(lines)
        except Exception as exc:
            logger.error(f"[cs_bet] open 失败: {exc}")
            return f"开盘失败（{type(exc).__name__}）。如实说没开成。"

    @llm_tool(name="cs_bet_place")
    async def cs_bet_place(
        self,
        event: AstrMessageEvent,
        who: str,
        winner: str = "",
        series: str = "",
        ot: str = "",
    ) -> str:
        """登记一注。who="her" 是她的选择，who="user" 是用户的。

        用户说"我押 X"、"我猜 2:1"时用 who="user"；
        她自己表态时用 who="her"（她可以推脱、可以硬押、可以跟用户反着来）。

        Args:
            who(string): her 或 user
            winner(string): 押谁赢，填队名
            series(string): 大比分，例如 "2:0"、"2:1"
            ot(string): 押有没有加时，填 "有" 或 "没有"
        """
        who = "her" if str(who).strip().lower() in ("her", "she", "她", "self", "bot") else "user"
        bet = next((b for b in reversed(self._state["bets"]) if b.get("status") == "open"), None)
        if bet is None:
            return "没有开着的盘口。先用 cs_bet_open 开一个。"
        p = bet["picks"].setdefault(who, {})
        if winner:
            p["winner"] = winner.strip()
        if series:
            p["series"] = series.strip().replace("：", ":")
        if ot:
            p["ot"] = ot.strip()
        self._save()

        label = "她" if who == "her" else "用户"
        mine = bet["picks"].get("her") or {}
        yours = bet["picks"].get("user") or {}
        if mine and yours:
            return (
                f"{label}这注记下了：{p}。两边都齐了 —— "
                f"她 {mine}，用户 {yours}。可以开始了。"
            )
        return f"{label}这注记下了：{p}。另一边还没押。"

    @llm_tool(name="cs_bet_settle")
    async def cs_bet_settle(self, event: AstrMessageEvent) -> str:
        """结算所有开着的盘口，拿真实赛果算分，更新战绩与连胜。

        比赛结束后（用户问"谁赢了/我赢没赢/结算一下"、或她发现打完了）调用。
        返回的账本可以直接用，但要用她自己的语气念，别照抄。
        """
        open_bets = [b for b in self._state["bets"] if b.get("status") == "open"]
        if not open_bets:
            return "没有待结算的盘口。"
        try:
            finished = [norm(r) for r in await self._fe.fetch(2, limit=50)]
            live = [norm(r) for r in await self._fe.fetch(1, limit=60)]
        except Exception as exc:
            return f"结算要查赛果，接口这会儿没返回（{type(exc).__name__}）。先别算，说等会儿。"

        pool = {m["id"]: m for m in finished + live}
        out = []
        for bet in open_bets:
            m = pool.get(bet.get("match_id"))
            if not m or m["status"] != "2":
                out.append(f"{bet['t1']} vs {bet['t2']} 还没打完，先不算。")
                continue
            res = {
                "winner": m["t1"] if m["s1"] > m["s2"] else m["t2"],
                "series": f"{m['s1']}:{m['s2']}",
                "ot": "有" if has_overtime(m) else "没有",
            }
            bet["result"] = res
            bet["status"] = "settled"
            bet["settled_ts"] = time.time()

            round_scores = {}
            for who in ("her", "user"):
                picks = bet["picks"].get(who) or {}
                pts = 0
                detail = []
                if picks.get("winner"):
                    ok = picks["winner"].strip().lower() in res["winner"].lower()
                    pts += 1 if ok else 0
                    detail.append(("赢" if ok else "输") + f"·胜负")
                if picks.get("series") and "series" in bet["markets"]:
                    ok = picks["series"].replace(" ", "") == res["series"]
                    pts += 1 if ok else 0
                    detail.append(("赢" if ok else "输") + "·比分")
                if picks.get("ot"):
                    ok = picks["ot"].strip() in res["ot"] or (picks["ot"].strip() == "没" and res["ot"] == "没有")
                    pts += 1 if ok else 0
                    detail.append(("赢" if ok else "输") + "·加时")
                round_scores[who] = (pts, detail)

            hp, hd = round_scores["her"]
            up, ud = round_scores["user"]
            for who, pts in (("her", hp), ("user", up)):
                st = self._state["score"].setdefault(who, {"pts": 0, "rounds": 0})
                st["pts"] = int(st.get("pts", 0)) + pts
                st["rounds"] = int(st.get("rounds", 0)) + 1
            if hp != up:
                win, lose = ("her", "user") if hp > up else ("user", "her")
                self._state["streak"][win] = int(self._state["streak"].get(win, 0)) + 1
                self._state["streak"][lose] = 0
            verdict = "她赢" if hp > up else ("用户赢" if up > hp else "平")
            out.append(
                f"{bet['t1']} {res['series']} {bet['t2']}（加时：{res['ot']}）｜"
                f"她 {hp} 分（{','.join(hd) or '没押'}），用户 {up} 分（{','.join(ud) or '没押'}）→ {verdict}"
            )
        self._save()
        board = self._board_text()
        return "【结算】\n" + "\n".join(out) + "\n" + board

    @llm_tool(name="cs_bet_board")
    async def cs_bet_board(self, event: AstrMessageEvent) -> str:
        """看打赌战绩：累计得分、场数、连胜、欠着的彩头。

        用户问"我赢几把了/谁厉害/账本呢"，或者她想挤兑用户时调用。
        """
        return self._board_text()

    @llm_tool(name="cs_bet_forfeit")
    async def cs_bet_forfeit(
        self, event: AstrMessageEvent, text: str, owed_by: str, done: bool = False
    ) -> str:
        """记一条彩头（输的人欠的事），可以标成已兑现。

        Args:
            text(string): 欠的是什么事，例如 "下次看球前先给我削个苹果"
            owed_by(string): 谁欠 her 或 user
            done(bool): 是否已经兑现
        """
        by = "her" if str(owed_by).strip().lower() in ("her", "她", "self", "bot") else "user"
        item = {"id": uuid.uuid4().hex[:8], "text": text.strip(), "owed_by": by, "created_ts": time.time(), "done": bool(done)}
        self._state.setdefault("forfeits", []).append(item)
        self._state["forfeits"] = self._state["forfeits"][-40:]
        self._save()
        who = "她" if by == "her" else "用户"
        return f"记下了：{who}欠「{text}」" + ("（已兑现）" if done else "（还欠着）")

    def _board_text(self) -> str:
        sc = self._state.get("score") or {}
        her = sc.get("her") or {}
        usr = sc.get("user") or {}
        streak = self._state.get("streak") or {}
        pending = [f for f in (self._state.get("forfeits") or []) if not f.get("done")]
        lines = [
            f"【战绩】她 {her.get('pts', 0)} 分 / {her.get('rounds', 0)} 场，"
            f"用户 {usr.get('pts', 0)} 分 / {usr.get('rounds', 0)} 场"
        ]
        if streak.get("her"):
            lines.append(f"她连胜 {streak['her']} 场")
        if streak.get("user"):
            lines.append(f"用户连胜 {streak['user']} 场")
        if pending:
            lines.append("欠着的彩头：" + "；".join(
                ("她" if f.get("owed_by") == "her" else "用户") + f"欠「{f['text']}」" for f in pending[:6]
            ))
        return "\n".join(lines)

    @filter.command("bet")
    async def bet_cmd(self, event: AstrMessageEvent, arg: str = ""):
        """看账本：/bet"""
        yield event.plain_result(self._board_text())

    async def terminate(self):
        await self._fe.close()
