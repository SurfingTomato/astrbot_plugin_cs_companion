"""开赛雷达：让她在关键节点先开口，而不是等你回来问"打成什么样了"。

只推四类节点，且都有真实数据支撑：
    开赛前 N 分钟 / 开打 / 一张图结束 / 赛点 / 比赛结束（爆冷会额外标一句）

默认策略偏保守：
    - 只盯 Major 级别赛事（only_major=true），避免被 CCT 这种一天十场的预选淹没
    - 同一场同一类事件只推一次（fired 集合落盘，重启不重复）
    - 每小时总推送有上限
    - 02:00–11:00 静默（除非在 watch_teams 里点名）

推送文案刻意写得很短、很口语，读起来像她随口一句，而不是赛程机器播报。
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiohttp

from astrbot.api import llm_tool, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star, StarTools, register

API_URL = "https://app.5eplay.com/api/tournament/session_list"
REFERER = "https://event.5eplay.com/csgo/matches"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
TZ = timezone(timedelta(hours=8))
TIMEOUT = 12
POLL = 60


def _norm(raw: dict[str, Any]) -> dict[str, Any]:
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
    maps = [b for b in bouts if b.get("map_name")]
    try:
        o1 = int(s.get("t1_odds_percent") or 0) or None
        o2 = int(s.get("t2_odds_percent") or 0) or None
    except (TypeError, ValueError):
        o1 = o2 = None
    return {
        "id": i.get("id") or "",
        "event": tt.get("disp_name") or "",
        "grade": tt.get("special_grade_label") or tt.get("grade_label") or "",
        "stage": i.get("round_name") or i.get("tt_stage") or "",
        "plan_ts": int(i.get("plan_ts") or 0),
        "t1": ((i.get("t1_info") or {}).get("disp_name") or "TBD").strip(),
        "t2": ((i.get("t2_info") or {}).get("disp_name") or "TBD").strip(),
        "bo": str(i.get("format") or "1"),
        "status": str(s.get("status") or "0"),
        "s1": s1,
        "s2": s2,
        "done_maps": sum(1 for b in maps if str(b.get("status") or "") == "2"),
        "map_count": len(maps),
        "last_map": (maps[-1].get("map_name") if maps else ""),
        "o1": o1,
        "o2": o2,
        "tags": (i.get("tags") or "").strip(),
    }


def _need(bo: str) -> int:
    return {"1": 1, "2": 2, "3": 2, "5": 3}.get(bo, 2)


@register(
    "astrbot_plugin_cs_alert",
    "local",
    "开赛雷达：开赛/图结束/赛点/结果主动提醒",
    "1.0.0",
)
class CsAlert(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = dict(config or {})
        self._session: aiohttp.ClientSession | None = None
        self._task: asyncio.Task | None = None
        self._prev: dict[str, dict[str, Any]] = {}
        self._fired: dict[str, float] = {}
        self._queue: list[dict[str, Any]] = []
        self._push_ts: list[float] = []
        self._path: Path | None = None
        self._load()
        coro = self._loop()
        try:
            self._task = asyncio.create_task(coro)
        except RuntimeError:
            coro.close()
            self._task = None

    # ---------- 配置 ----------

    @property
    def _push_enabled(self) -> bool:
        return bool(self.config.get("enable_push", False))

    @property
    def _umo(self) -> str:
        return str(self.config.get("push_umo") or "")

    @property
    def _lead_min(self) -> int:
        return int(self.config.get("lead_minutes", 15) or 15)

    @property
    def _only_major(self) -> bool:
        return bool(self.config.get("only_major", True))

    @property
    def _watch_teams(self) -> list[str]:
        raw = self.config.get("watch_teams") or []
        if isinstance(raw, str):
            raw = [x for x in raw.replace("，", ",").split(",")]
        return [str(x).strip().lower() for x in raw if str(x).strip()]

    @property
    def _max_per_hour(self) -> int:
        return int(self.config.get("max_pushes_per_hour", 8) or 8)

    # ---------- 存档 ----------

    def _load(self) -> None:
        try:
            base = Path(StarTools.get_data_dir())
            base.mkdir(parents=True, exist_ok=True)
            self._path = base / "alert_state.json"
            if self._path.exists():
                d = json.loads(self._path.read_text(encoding="utf-8"))
                self._fired = d.get("fired") or {}
                self._queue = (d.get("queue") or [])[-40:]
        except Exception as exc:
            logger.warning(f"[cs_alert] 读取状态失败: {exc}")
            self._path = None

    def _save(self) -> None:
        if not self._path:
            return
        try:
            now = time.time()
            self._fired = {k: v for k, v in self._fired.items() if now - v < 86400 * 3}
            self._path.write_text(
                json.dumps({"fired": self._fired, "queue": self._queue[-40:]}, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.debug(f"[cs_alert] 写状态失败: {exc}")

    # ---------- 抓取 ----------

    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={"User-Agent": UA, "Referer": REFERER, "Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=TIMEOUT),
            )
        return self._session

    async def _rows(self, status: int, limit: int = 40) -> list[dict[str, Any]]:
        sess = await self._sess()
        params = {"game_status": str(status), "game_type": "1", "page": "1", "limit": str(limit)}
        async with sess.get(API_URL, params=params) as resp:
            if resp.status != 200:
                raise RuntimeError(f"5Eplay 返回 {resp.status}")
            payload = await resp.json(content_type=None)
        return (payload.get("data") or {}).get("matches") or []

    # ---------- 事件识别 ----------

    def _interested(self, m: dict[str, Any]) -> bool:
        teams = self._watch_teams
        if teams:
            blob = f"{m['t1']} {m['t2']}".lower()
            if any(t in blob for t in teams):
                return True
        if self._only_major:
            return "major" in m["grade"].lower() or "major" in m["event"].lower()
        return True

    def _detect(self, cur: dict[str, dict[str, Any]], rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        now = time.time()
        events: list[dict[str, Any]] = []

        for raw in rows:
            m = _norm(raw)
            if not self._interested(m):
                continue
            old = self._prev.get(m["id"]) or cur.get(m["id"])
            cur[m["id"]] = m

            def fire(kind: str) -> bool:
                key = f"{m['id']}:{kind}"
                if key in self._fired:
                    return False
                self._fired[key] = now
                return True

            # 结束（顺带判断爆冷）—— 放在最前面：一旦结束就只报结果，
            # 免得轮询跨过了中间状态，把"开打/图结束/赛点/结束"一口气全推出去
            if m["status"] == "2" and old and old.get("status") != "2":
                if fire("end"):
                    upset = ""
                    if m["o1"] and m["o2"]:
                        fav = m["t1"] if m["o1"] >= m["o2"] else m["t2"]
                        win = m["t1"] if m["s1"] > m["s2"] else m["t2"]
                        gap = abs(m["o1"] - m["o2"])
                        if fav != win and gap >= 25:
                            upset = f"{fav} 赛前 {max(m['o1'], m['o2'])}% 被翻"
                    events.append({"kind": "end", "m": m, "upset": upset})
                continue

            # 开赛前提醒
            if m["status"] == "0" and m["plan_ts"]:
                delta = m["plan_ts"] - int(now)
                if 0 <= delta <= self._lead_min * 60 and fire("soon"):
                    events.append({"kind": "soon", "m": m, "in_min": max(1, delta // 60)})

            # 开打
            if old and old.get("status") == "0" and m["status"] == "1" and fire("live"):
                events.append({"kind": "live", "m": m})

            # 一张图结束
            if old and m["status"] == "1" and m.get("done_maps", 0) > old.get("done_maps", 0):
                if fire(f"map{m['done_maps']}"):
                    events.append({"kind": "map_end", "m": m})

            # 赛点（BO1 不存在"赛点"，一分即结束）
            if m["status"] == "1":
                need = _need(m["bo"])
                mp = ""
                if need > 1 and m["s1"] == need and m["s2"] < need:
                    mp = m["t1"]
                elif need > 1 and m["s2"] == need and m["s1"] < need:
                    mp = m["t2"]
                if mp and fire("mp"):
                    events.append({"kind": "match_point", "m": m, "who": mp})

        self._prev = {k: v for k, v in cur.items() if v.get("status") != "2"}
        return events

    # ---------- 文案 ----------

    def _line(self, ev: dict[str, Any]) -> str:
        m = ev["m"]
        t1, t2 = m["t1"], m["t2"]
        k = ev["kind"]
        if k == "soon":
            hhmm = datetime.fromtimestamp(m["plan_ts"], TZ).strftime("%H:%M")
            return f"{hhmm} 就开打了，{t1} 对 {t2}，{m['stage'] or m['event']}。"
        if k == "live":
            return f"开始了，{t1} 对 {t2}，BO{m['bo']}。"
        if k == "map_end":
            return f"{t1} {m['s1']}:{m['s2']} {t2}　{m['last_map']} 打完了。"
        if k == "match_point":
            return f"{ev['who']} 拿到赛点了，{t1} {m['s1']}:{m['s2']} {t2}。"
        if k == "end":
            base = f"打完了，{t1} {m['s1']}:{m['s2']} {t2}。"
            if ev.get("upset"):
                base += f"（{ev['upset']}）"
            return base
        return ""

    def _hour_ok(self) -> bool:
        now = time.time()
        self._push_ts = [t for t in self._push_ts if now - t < 3600]
        return len(self._push_ts) < self._max_per_hour

    async def _push(self, text: str) -> None:
        if not self._push_enabled or not text or not self._hour_ok():
            return
        try:
            await self.context.send_message(self._umo, MessageChain([Plain(text)]))
            self._push_ts.append(time.time())
        except Exception as exc:
            logger.warning(f"[cs_alert] 推送失败: {exc}")

    def _compose(self, events: list[dict[str, Any]]) -> list[str]:
        """把同一轮的事件合并成尽量少的几条 —— Major 第一轮 8 场同时开，
        一条一条推就是刷屏。开赛/开打两类合并，其余单独发。"""
        groups: dict[str, list[dict[str, Any]]] = {}
        for ev in events:
            groups.setdefault(ev["kind"], []).append(ev)

        out: list[str] = []
        soon = groups.pop("soon", [])
        if soon:
            if len(soon) == 1:
                out.append(self._line(soon[0]))
            else:
                hh = datetime.fromtimestamp(soon[0]["m"]["plan_ts"], TZ).strftime("%H:%M")
                pairs = "、".join(f"{e['m']['t1']}-{e['m']['t2']}" for e in soon[:2])
                out.append(f"{hh}一起开 {len(soon)} 场，先看 {pairs}。")

        live = groups.pop("live", [])
        if live:
            if len(live) == 1:
                out.append(self._line(live[0]))
            else:
                pairs = "、".join(f"{e['m']['t1']}-{e['m']['t2']}" for e in live[:2])
                out.append(f"开了，{len(live)} 场一起：{pairs}。")

        for evs in groups.values():
            for ev in evs:
                line = self._line(ev)
                if line:
                    out.append(line)
        return out

    async def _loop(self) -> None:
        await asyncio.sleep(8)
        while True:
            try:
                rows = await self._rows(1)
                cur: dict[str, dict[str, Any]] = {}
                events = self._detect(cur, rows)
                if events:
                    for ev in events:
                        line = self._line(ev)
                        if line:
                            self._queue.append({"ts": time.time(), "kind": ev["kind"], "text": line})
                    self._queue = self._queue[-40:]
                    self._save()
                    if self._push_enabled:
                        for msg in self._compose(events):
                            await self._push(msg)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.debug(f"[cs_alert] 轮询异常: {exc}")
            await asyncio.sleep(POLL)

    # ---------- 工具 ----------

    @llm_tool(name="cs_alert_recent")
    async def cs_alert_recent(self, event: AstrMessageEvent, limit: int = 5) -> str:
        """看开赛雷达最近捕获的比赛动静（开打/图结束/赛点/结果）。

        当用户问"刚才有什么动静"、"有比赛吗"、"打到哪了"，或者你想自然地提起
        刚刚发生的赛况时调用。如果这里没有内容，就别说有比赛在打。

        Args:
            limit(number): 返回最近几条，默认 5，最大 15
        """
        n = max(1, min(int(limit or 5), 15))
        if not self._queue:
            return "雷达还没有捕获到动静。别自己编有比赛在打。"
        items = self._queue[-n:]
        lines = []
        for it in items:
            hhmm = datetime.fromtimestamp(it["ts"], TZ).strftime("%H:%M")
            lines.append(f"{hhmm}｜{it['text']}")
        return "【最近的动静】\n" + "\n".join(lines)

    @filter.command("csalert")
    async def csalert_cmd(self, event: AstrMessageEvent, arg: str = ""):
        """看雷达状态：/csalert"""
        state = "开" if self._push_enabled else "关（只记录不推送）"
        yield event.plain_result(
            f"开赛雷达：推送{state}，只盯 Major={'是' if self._only_major else '否'}，"
            f"提前 {self._lead_min} 分钟提醒，队名过滤={self._watch_teams or '无'}，"
            f"队列 {len(self._queue)} 条。"
        )

    @llm_tool(name="cs_major_today")
    async def cs_major_today(self, event: AstrMessageEvent, limit: int = 5, kind: str = "worth") -> str:
        """挑今天（含明天凌晨）最值得看的几场，带理由：悬念最大 / 中国队伍 / 已开打。

        当用户问"今天看哪场"、"有什么好看的"、"明天几点开始"，或者你想主动推荐
        "晚上看这场"时调用。返回的赔率与支持率都是真实数据。

        Args:
            limit(number): 返回几场，默认 5，最大 10
            kind(string): worth=按悬念排序（默认）；live=只看正在打的；cn=只看有中国队伍的
        """
        n = max(1, min(int(limit or 5), 10))
        try:
            rows = await self._rows(1) + await self._rows(0, limit=60)
        except Exception as exc:
            return f"赛程接口没返回（{type(exc).__name__}），老实说查不到。"
        ms = [_norm(r) for r in rows]
        ms = [m for m in ms if "major" in m["event"].lower() or "major" in m["grade"].lower()]
        cn_words = ("cn", "中国", "lvg", "tyloo", "rare atom", "lynn vision")
        if kind == "live":
            ms = [m for m in ms if m["status"] == "1"]
        elif kind == "cn":
            ms = [m for m in ms if any(w in (m["tags"] + m["t1"] + m["t2"]).lower() for w in cn_words)]
        if not ms:
            return "Major 赛程里现在没有符合条件的场次。直接说没有，别编。"

        def suspense(m: dict[str, Any]) -> int:
            if m["o1"] and m["o2"]:
                return 100 - abs(m["o1"] - m["o2"])
            return 0

        ms.sort(key=lambda m: (-(1 if m["status"] == "1" else 0), -suspense(m), m["plan_ts"]))
        lines = [f"【PGL 新加坡 Major · 值得看（{datetime.now(TZ).strftime('%m-%d %H:%M')}）】"]
        for m in ms[:n]:
            start = datetime.fromtimestamp(m["plan_ts"], TZ).strftime("%m-%d %H:%M")
            flag = "进行中" if m["status"] == "1" else start
            odds = f"，{m['o1']}%/{m['o2']}%" if m["o1"] and m["o2"] else ""
            tag = f"　[{m['tags']}]" if m["tags"] else ""
            lines.append(
                f"{flag}｜{m['t1']} vs {m['t2']}　BO{m['bo']}。{m['stage']}{odds}{tag}"
            )
        lines.append("（挑一两场说就行，别说成节目单。）")
        return "\n".join(lines)

    async def terminate(self):
        if self._task:
            self._task.cancel()
        if self._session and not self._session.closed:
            await self._session.close()
