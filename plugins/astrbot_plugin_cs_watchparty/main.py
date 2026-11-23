"""陪看台：让她"在场"，而不是每句话都去查一次比分。

问题：现有 cs_matches 是被动工具——你不问，她不知道比分；你问，她答数据，
    整段对话就变成"报分机器"。

做法：把"正在看的那场"变成一段持续存在的小状态：
    - 谁在打、她站哪边、她押了什么、她刚才说了什么判断
    - 每轮对话前把最新真实比分注入 system prompt（她自己就知道，不用调用工具）
    - 图间/赛点这类节奏点会被标出来，供她接话
    - 结束后生成小结，记进 history，第二天还能翻
"""

from __future__ import annotations

import asyncio
import json
import random
import time
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
CACHE_TTL = 40
SESSION_STATUS = {"0": "未开始", "1": "进行中", "2": "已结束"}
MAX_NOTES = 40
ACTIVE_TTL = 8 * 3600  # 超过 8 小时没动静，视为那场比赛已经散了

# 图间卡池。原则：只放不会过时的东西——赛制规则、图池常识、以及不会答错的问题。
# 任何"某选手某年打了几个 ace"这类会随版本和赛季失效的具体数据，一律不进。
GAP_CARDS: list[dict[str, str]] = [
    {"q": "一回合正赛是 1 分 55 秒，购买时间 20 秒。可以问她：开局那 20 秒她一般在想什么",
     "fact": "MR12 制"},
    {"q": "C4 下包后 40 秒爆炸，这个数字比很多人以为的长",
     "fact": "40 秒"},
    {"q": "拆包没钳子 10 秒、有钳子 5 秒 —— 所以最后一秒的假拆永远值钱",
     "fact": "钳子减半"},
    {"q": "12:12 进加时，加时是 MR3、先到 4 分。可以问她：你觉得加时更考验心态还是枪法",
     "fact": "加时先到 4 分"},
    {"q": "打满 12 回合换边。可以问她：换边后你更爱看哪边"},
    {"q": "图池（Active Duty）常年就那 7 张，所以每张图的固定套路才那么深",
     "fact": "7 张图"},
    {"q": "烟雾弹大概 18 秒，燃烧弹大概 7 秒 —— 进攻方的时间窗口基本就是拿这个算的",
     "fact": "约 18s / 7s"},
    {"q": "省一口气的经济局不是放弃，是攒下一把的枪。可以问她：你受得了队友硬 eco 吗"},
    {"q": "AWP 除了打腿基本一枪一个，所以架点的人比冲点的人更怕被发现",
     "fact": "躯干一枪"},
    {"q": "Major 小组赛多是 BO1、淘汰赛才 BO3，所以 BO1 里一个手枪局就能决定一整天",
     "fact": "BO1 容错极低"},
    {"q": "你觉得这行里最像'永远差一口气'的是哪支队"},
    {"q": "如果只能留一张图，你留哪张"},
    {"q": "你第一次看 CS 是哪一年，看的谁"},
    {"q": "看比赛你更在意操作还是配合"},
    {"q": "你打的时候最怕队友干什么"},
    {"q": "有没有哪场你看完当天睡不着"},
    {"q": "你觉得解说的哪句话最没用"},
    {"q": "如果让你挑一个选手的打法去学，你学谁"},
]


# ---------------------------------------------------------------- 5Eplay 客户端


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
        if not payload.get("success"):
            raise RuntimeError(f"5Eplay 报错: {payload.get('message')}")
        rows = (payload.get("data") or {}).get("matches") or []
        self._cache[status] = (time.time(), rows)
        return rows

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()


def norm(raw: dict[str, Any]) -> dict[str, Any]:
    """把接口对象压成只保留陪看用得上的字段。"""
    i = raw.get("mc_info") or {}
    s = raw.get("state") or {}
    tt = raw.get("tt_info") or {}
    bouts = s.get("bout_states") or []

    try:
        s1 = int(s.get("t1_score") or 0)
        s2 = int(s.get("t2_score") or 0)
    except (TypeError, ValueError):
        s1 = s2 = 0
    # 接口大分偶尔不跟地图结果走（见 cs_matches 的注释），取较大值兜底
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

    def _odds(v: Any) -> int | None:
        try:
            n = int(v)
        except (TypeError, ValueError):
            return None
        return n or None

    return {
        "id": i.get("id") or "",
        "event": tt.get("disp_name") or "",
        "grade": tt.get("special_grade_label") or tt.get("grade_label") or "",
        "stage": i.get("round_name") or i.get("tt_stage") or "",
        "plan_ts": int(i.get("plan_ts") or 0),
        "t1": ((i.get("t1_info") or {}).get("disp_name") or "TBD").strip(),
        "t2": ((i.get("t2_info") or {}).get("disp_name") or "TBD").strip(),
        "bo": str(i.get("format") or ""),
        "status": str(s.get("status") or "0"),
        "s1": s1,
        "s2": s2,
        "maps": maps,
        "o1": _odds(s.get("t1_odds_percent")),
        "o2": _odds(s.get("t2_odds_percent")),
        "tags": (i.get("tags") or "").strip(),
    }


def pick(rows: list[dict[str, Any]], query: str) -> dict[str, Any] | None:
    """按队名/赛事名/场次 id 找一场：优先进行中，其次未开始，最后最近结束。"""
    q = (query or "").strip().lower()
    pool = [norm(r) for r in rows]
    if q:
        hit = []
        for m in pool:
            blob = " ".join([m["t1"], m["t2"], m["event"], m["stage"], m["id"]]).lower()
            if q in blob:
                hit.append(m)
        pool = hit
    if not pool:
        return None
    order = {"1": 0, "0": 1, "2": 2}
    pool.sort(key=lambda m: (order.get(m["status"], 3), m["plan_ts"]))
    return pool[0]


def score_line(m: dict[str, Any]) -> str:
    return f"{m['t1']} {m['s1']}:{m['s2']} {m['t2']}"


def map_line(m: dict[str, Any]) -> str:
    out = []
    for mp in m["maps"]:
        seg = f"图{mp['n']} {mp['name']} {mp['s1']}:{mp['s2']}"
        if mp["done"]:
            seg += "✓"
        out.append(seg)
    return "，".join(out)


def match_point(m: dict[str, Any]) -> str:
    """判断赛点/结束这类节奏点，只描述事实。"""
    if m["status"] == "2":
        return "已结束"
    if m["status"] != "1":
        return ""
    bo = m["bo"]
    need = {"1": 1, "2": 2, "3": 2, "5": 3}.get(bo, 2)
    # BO1 拿到 1 分就是赢了，不存在"赛点"
    if need > 1 and m["s1"] == need and m["s2"] < need:
        return f"{m['t1']} 已到赛点"
    if need > 1 and m["s2"] == need and m["s1"] < need:
        return f"{m['t2']} 已到赛点"
    cur = m["maps"][-1] if m["maps"] else None
    if cur and not cur["done"]:
        try:
            a, b = int(cur["s1"]), int(cur["s2"])
        except ValueError:
            return ""
        if max(a, b) == 12 and min(a, b) <= 10:
            return f"图{cur['n']} 到 12:{min(a, b)}，赛点局"
    return ""


# ---------------------------------------------------------------- 插件


@register(
    "astrbot_plugin_cs_watchparty",
    "local",
    "陪看台：把「正在看的这场」变成持续状态，让她真的在场",
    "1.0.0",
)
class CsWatchParty(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = dict(config or {})
        self._fe = FiveE()
        self._path: Path | None = None
        self._state: dict[str, Any] = {"active": None, "history": [], "gap_used": []}
        self._load()
        self._inject_limit = int(self.config.get("inject_char_limit", 320) or 320)
        coro = self._gap_loop()
        try:
            self._gap_task = asyncio.create_task(coro)
        except RuntimeError:
            coro.close()
            self._gap_task = None

    # ---------- 存档 ----------

    def _load(self) -> None:
        try:
            base = Path(StarTools.get_data_dir())
            base.mkdir(parents=True, exist_ok=True)
            self._path = base / "watch_state.json"
            if self._path.exists():
                self._state = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning(f"[watchparty] 读取状态失败: {exc}")
            self._path = None
        if not isinstance(self._state.get("history"), list):
            self._state["history"] = []

    def _save(self) -> None:
        if not self._path:
            return
        try:
            self._path.write_text(
                json.dumps(self._state, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning(f"[watchparty] 写状态失败: {exc}")

    def _active(self) -> dict[str, Any] | None:
        act = self._state.get("active")
        if not isinstance(act, dict):
            return None
        if time.time() - float(act.get("touched_ts") or 0) > ACTIVE_TTL:
            return None
        return act

    # ---------- 刷新 ----------

    async def _refresh(self, act: dict[str, Any]) -> dict[str, Any] | None:
        """按 id 重新拉这场的当前状态。找不到就退化成只拉今日，按队名匹配。"""
        try:
            rows = await self._fe.fetch(1) + await self._fe.fetch(2, limit=40)
        except Exception:
            return None
        target = act.get("match_id")
        for raw in rows:
            m = norm(raw)
            if m["id"] == target:
                if m["status"] != "2":
                    try:
                        upcoming = await self._fe.fetch(0, limit=60)
                        for u in upcoming:
                            nu = norm(u)
                            if nu["id"] == target:
                                m = nu
                                break
                    except Exception:
                        pass
                act["last"] = m
                act["touched_ts"] = time.time()
                return m
        return act.get("last")

    # ---------- 工具 ----------

    @llm_tool(name="cs_watch_start")
    async def cs_watch_start(
        self,
        event: AstrMessageEvent,
        match: str = "",
        side: str = "",
        stance: str = "",
    ) -> str:
        """进入「陪看模式」：锁定一场比赛，之后你不用每次问比分。

        当用户说"一起看"、"开始了"、"我开看了"、"陪我看"、点名某场/某队，
        或者你们正在聊某场比赛、准备看，就调用本工具。

        一场只锁一场。锁上之后，每轮对话前系统会自动把真实比分告诉模型，
        所以不要再用 cs_matches 反复查同一场。

        Args:
            match(string): 用队名或赛事名指定，例如 "FaZe"、"9z"、"新加坡 Major"；留空=当前正在打的那场
            side(string): 你们支持/站哪一边，填队名。用户没明说就留空
            stance(string): 她自己的立场或态度，一句口语即可，例如 "我押 FaZe 但心里有点虚"
        """
        try:
            rows = await self._fe.fetch(1)
            m = pick(rows, match)
            if m is None and match:
                rows2 = await self._fe.fetch(0, limit=60)
                m = pick(rows2, match)
            if m is None:
                return "现在没有能锁的比赛。回用户一句实话：赛程里没找到，不要编。"

            old = self._active()
            if old and old.get("match_id") != m["id"]:
                self._archive(old)

            start = datetime.fromtimestamp(m["plan_ts"], TZ).strftime("%m-%d %H:%M")
            act = {
                "match_id": m["id"],
                "event": m["event"],
                "stage": m["stage"],
                "t1": m["t1"],
                "t2": m["t2"],
                "bo": m["bo"],
                "start_at": start,
                "side": side.strip(),
                "stance": stance.strip(),
                "notes": [],
                "opened_ts": time.time(),
                "touched_ts": time.time(),
                "last": m,
            }
            self._state["active"] = act
            self._save()

            bits = [
                f"已进入陪看模式。{m['event']} {m['stage']}｜{m['t1']} vs {m['t2']}"
                f"（BO{m['bo']}，{start}）",
                f"当前 {score_line(m)}（{SESSION_STATUS.get(m['status'], '?')}）",
            ]
            if map_line(m):
                bits.append(map_line(m))
            if act["side"]:
                bits.append(f"你站 {act['side']}")
            if act["stance"]:
                bits.append(f"你的话：「{act['stance']}」")
            bits.append("接下来的对话会自动带上比分，你不需要再查。用你自己的语气开口，别播报。")
            return "\n".join(bits)
        except Exception as exc:
            logger.error(f"[watchparty] start 失败: {exc}")
            return f"锁比赛失败（{type(exc).__name__}）。如实说没查到，不要编。"

    @llm_tool(name="cs_watch_now")
    async def cs_watch_now(self, event: AstrMessageEvent, detail: bool = True) -> str:
        """看当前陪看的那场实时打到哪了（含地图逐图比分、赛点、她的立场和她记过的判断）。

        用户问"现在几比几/打到哪了/这张图怎么样/还有戏吗"时调用。
        返回的是真实数据，可以直接用。

        Args:
            detail(bool): true=带逐图比分，false=只要大分
        """
        act = self._active()
        if not act:
            return "现在没有锁定的比赛。可以用 cs_watch_start 锁一场，或者直接说没在看。"
        m = await self._refresh(act)
        if not m:
            return "比分接口这会儿没返回。老实说暂时看不到，别猜。"
        self._save()

        lines = [
            f"{m['event']} {m['stage']}｜{score_line(m)}（{SESSION_STATUS.get(m['status'], '?')}）"
        ]
        if detail and map_line(m):
            lines.append(map_line(m))
        mp = match_point(m)
        if mp:
            lines.append(mp)
        if act.get("side"):
            lines.append(f"你站 {act['side']}")
        recent = [f"「{n['text']}」" for n in (act.get("notes") or [])[-3:]]
        if recent:
            lines.append("你之前说过：" + "、".join(recent))
        return "\n".join(lines)

    @llm_tool(name="cs_watch_note")
    async def cs_watch_note(self, event: AstrMessageEvent, text: str, kind: str = "判断") -> str:
        """把她在看比赛时冒出的念头记下来，之后会被带进上下文（相当于她的看球手记）。

        当她说出一个判断、吐槽、心疼某队、临场改口、或者跟你打赌时调用。
        这些记录会在后面自动出现，让她显得"记得自己刚才说过什么"。

        Args:
            text(string): 她说的话，尽量保留原话，不要改写
            kind(string): 判断 / 吐槽 / 心疼 / 改口 / 打赌
        """
        act = self._active()
        if not act:
            return "没在陪看，这条不用记。"
        act.setdefault("notes", []).append(
            {"ts": time.time(), "kind": (kind or "判断").strip(), "text": (text or "").strip()[:120]}
        )
        act["notes"] = act["notes"][-MAX_NOTES:]
        act["touched_ts"] = time.time()
        self._save()
        return f"记下了（{kind}）：{text}"

    @llm_tool(name="cs_watch_end")
    async def cs_watch_end(self, event: AstrMessageEvent, verdict: str = "") -> str:
        """结束陪看，生成一份小结存进历史（比分、她的立场、她记的所有判断）。

        用户说"看完了"、"不看了"、"睡了"、比赛结束后聊完时调用。
        工具会返回小结素材，她用自己语气说一遍即可，不要照读。

        Args:
            verdict(string): 她的一句话总结/情绪，可选
        """
        act = self._active()
        if not act:
            return "本来就没在陪看，不用收尾。"
        m = await self._refresh(act) or act.get("last") or {}
        summary = self._summary(act, m, verdict)
        self._archive(act, m, verdict)
        self._state["active"] = None
        self._save()
        return summary

    def _summary(self, act: dict[str, Any], m: dict[str, Any], verdict: str) -> str:
        lines = ["【这场看完了，小结素材】"]
        if m:
            lines.append(f"{m['event']} {m['stage']}｜{score_line(m)}（{SESSION_STATUS.get(m['status'], '?')}）")
            if map_line(m):
                lines.append(map_line(m))
        if act.get("side"):
            lines.append(f"你站 {act['side']}")
        if act.get("stance"):
            lines.append(f"开场时你说：「{act['stance']}」")
        notes = act.get("notes") or []
        if notes:
            lines.append(f"中间你记了 {len(notes)} 条：" + "；".join(f"「{n['text']}」" for n in notes[-6:]))
        if verdict:
            lines.append(f"收尾这句：「{verdict}」")
        lines.append("用你自己的话讲一遍，别念清单。")
        return "\n".join(lines)

    def _archive(self, act: dict[str, Any], m: dict[str, Any] | None = None, verdict: str = "") -> None:
        rec = {
            "match_id": act.get("match_id"),
            "event": act.get("event"),
            "stage": act.get("stage"),
            "teams": f"{act.get('t1')} vs {act.get('t2')}",
            "score": f"{m.get('s1', '?')}:{m.get('s2', '?')}" if m else "",
            "maps": map_line(m) if m else "",
            "side": act.get("side", ""),
            "stance": act.get("stance", ""),
            "notes": (act.get("notes") or [])[-12:],
            "verdict": verdict,
            "closed_ts": time.time(),
        }
        self._state.setdefault("history", []).append(rec)
        self._state["history"] = self._state["history"][-60:]

    # ---------- 图间空档 ----------
    #
    # 图与图之间那三五分钟是最容易冷掉的地方：比分还没出、话头接不上，
    # 两个人就一起安静地看广告。
    # 这里的规矩是：**只准用工具给的真实比分 + 固定卡池里的常识，不许自己补数字。**

    @property
    def _gap_enabled(self) -> bool:
        return bool(self.config.get("enable_gap_push", True))

    @property
    def _gap_umo(self) -> str:
        return str(self.config.get("gap_umo") or "")

    @property
    def _gap_delay(self) -> int:
        return int(self.config.get("gap_delay_seconds", 75) or 75)

    def _gap_line(self, m: dict[str, Any], act: dict[str, Any]) -> str:
        """图间那一句。短，且只带真实比分。"""
        done = [mp for mp in m["maps"] if mp["done"]]
        if not done:
            return ""
        cur = done[-1]
        s1, s2 = cur["s1"], cur["s2"]
        try:
            a, b = int(s1), int(s2)
        except ValueError:
            a = b = 0
        lead = m["t1"] if a > b else (m["t2"] if b > a else "")
        tail = f"{lead} 先拿一张。" if lead else f"{cur['name']} 打平了。"
        side = act.get("side") or ""
        picks = [
            f"{cur['name']} 完了，{m['t1']} {s1}:{s2} {m['t2']}。{tail}",
            f"图间了。{cur['name']} 这个 {s1}:{s2}，你怎么看？",
            f"歇几分钟。刚那张 {cur['name']} 打成 {s1}:{s2}。",
        ]
        if side:
            picks.append(f"图间。刚那张 {cur['name']} 是 {s1}:{s2}，我有点想法，等你说。")
        return random.choice(picks)

    def _gap_material(self, m: dict[str, Any], act: dict[str, Any]) -> list[str]:
        """给她三条可抛的话头：两条来自刚打完的图，一条来自卡池。"""
        out: list[str] = []
        done = [mp for mp in m["maps"] if mp["done"]]
        if done:
            cur = done[-1]
            try:
                a, b = int(cur["s1"]), int(cur["s2"])
            except ValueError:
                a = b = 0
            if max(a, b) - min(a, b) <= 2 and max(a, b) >= 11:
                out.append(
                    f"刚那张 {cur['name']} 是 {cur['s1']}:{cur['s2']}，咬得很紧 —— "
                    "可以问她哪一局最揪心，别自己编局面细节"
                )
            elif max(a, b) - min(a, b) >= 7:
                out.append(
                    f"刚那张 {cur['name']} 是 {cur['s1']}:{cur['s2']}，一边倒了 —— "
                    "可以问要不要换台/还看不看，别说成解说"
                )
            else:
                out.append(f"刚那张 {cur['name']} 是 {cur['s1']}:{cur['s2']}，可以说一句就停")

        card = self._pick_card()
        if card:
            out.append(f"卡池这张：{card['q']}")

        if act.get("notes"):
            out.append(f"可以翻旧账：你自己刚说过「{act['notes'][-1]['text']}」")

        out.append("规矩：只准用上面出现的数字和比分；别编选手数据、别编战术细节、别说成解说。")
        return out

    def _pick_card(self) -> dict[str, str] | None:
        pool = [c for i, c in enumerate(GAP_CARDS) if i not in self._state.get("gap_used", [])]
        if not pool:
            self._state["gap_used"] = []
            pool = list(GAP_CARDS)
        card = random.choice(pool)
        idx = GAP_CARDS.index(card)
        used = self._state.setdefault("gap_used", [])
        used.append(idx)
        self._state["gap_used"] = used[-len(GAP_CARDS):]
        return card

    @llm_tool(name="cs_gap_talk")
    async def cs_gap_talk(self, event: AstrMessageEvent) -> str:
        """图与图之间没话说时，从真实比分和固定卡池里取几条可抛的话头。

        当比赛在两图之间、暂停、或者用户沉默了一阵，调用它拿素材。
        素材里的比分是真的，卡池里的常识是真的；除此之外不要自己补数字。
        """
        act = self._active()
        if not act:
            return "现在没在陪看，不用找话头。"
        m = await self._refresh(act)
        if not m:
            return "比分接口没返回，先别找话头，容易说错。"
        material = self._gap_material(m, act)
        return "【图间可用的话头】\n" + "\n".join(f"- {x}" for x in material)

    async def _gap_loop(self) -> None:
        await asyncio.sleep(30)
        while True:
            try:
                act = self._active()
                if act and act.get("match_id"):
                    m = await self._refresh(act)
                    if m:
                        done = sum(1 for mp in m["maps"] if mp["done"])
                        if done > int(act.get("gap_done", 0)):
                            act["gap_done"] = done
                            act["gap_pending_ts"] = time.time()
                            self._save()
                        elif act.get("gap_pending_ts") and m["status"] == "1":
                            if time.time() - float(act["gap_pending_ts"]) >= self._gap_delay:
                                line = self._gap_line(m, act)
                                act.pop("gap_pending_ts", None)
                                self._save()
                                if line and self._gap_enabled:
                                    await self._push_gap(line)
                        elif m["status"] == "2":
                            act.pop("gap_pending_ts", None)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug(f"[watchparty] 空档轮询异常: {exc}")
            await asyncio.sleep(30)

    async def _push_gap(self, text: str) -> None:
        if self.context is None or not text:
            return
        try:
            from astrbot.api.event import MessageChain
            from astrbot.api.message_components import Plain

            await self.context.send_message(self._gap_umo, MessageChain([Plain(text)]))
        except Exception as exc:
            logger.debug(f"[watchparty] 图间推送失败: {exc}")

    # ---------- 注入 ----------

    @filter.on_llm_request()
    async def inject_watch_context(self, event: AstrMessageEvent, req=None) -> None:
        """每轮把当前比分塞进 system prompt —— 这是"在场感"的全部来源。"""
        try:
            act = self._active()
            if not act or req is None:
                return
            m = await self._refresh(act)
            if not m:
                return
            self._save()

            seg = [f"【陪看台·真实数据，只作背景】{m['event']} {m['stage']} {score_line(m)}"]
            st = SESSION_STATUS.get(m["status"], "")
            if st:
                seg.append(f"（{st}）")
            if m["maps"]:
                seg.append("｜" + map_line(m))
            mp = match_point(m)
            if mp:
                seg.append("｜" + mp)
            if m["status"] == "1" and m["maps"] and m["maps"][-1]["done"]:
                seg.append("｜图间休息中（可以找个话头，或直接 cs_gap_talk 取素材）")
            if act.get("side"):
                seg.append(f"｜你站 {act['side']}")
            notes = (act.get("notes") or [])[-3:]
            if notes:
                seg.append("｜你刚说过：" + "、".join(f"「{n['text']}」" for n in notes))

            text = "".join(seg)
            if len(text) > self._inject_limit:
                text = text[: self._inject_limit] + "…"
            text += (
                "\n（别播报数据、别每句都提比分；只有跟此刻气氛对得上时才自然带一句，"
                "比分以这段为准，绝不自己编或圆场。）"
            )
            req.system_prompt = (req.system_prompt or "") + "\n\n" + text
        except Exception as exc:
            logger.debug(f"[watchparty] 注入失败: {exc}")

    # ---------- 指令 ----------

    @filter.command("watch")
    async def watch_cmd(self, event: AstrMessageEvent, arg: str = ""):
        """查当前陪看状态：/watch"""
        act = self._active()
        if not act:
            yield event.plain_result("现在没在陪看。")
            return
        m = await self._refresh(act)
        if not m:
            yield event.plain_result("接口这会儿没返回比分。")
            return
        body = [f"{m['event']} {m['stage']}", score_line(m)]
        if map_line(m):
            body.append(map_line(m))
        if act.get("side"):
            body.append(f"站 {act['side']}")
        yield event.plain_result("\n".join(body))

    async def terminate(self):
        if getattr(self, "_gap_task", None):
            self._gap_task.cancel()
        await self._fe.close()
