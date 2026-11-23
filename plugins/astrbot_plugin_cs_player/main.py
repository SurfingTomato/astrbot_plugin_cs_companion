"""选手簿：看比赛时认人用的。

数据源是 Liquipedia 的 Counter-Strike wiki（action=parse 取 wikitext），
不是实时比分。所以这里只回答"这个人是谁、在哪个队、在队多久、以前待过哪"，
以及"这支队现在首发是谁、什么时候换的人"。

诚实边界（写在每个工具的返回值里）：
- 花名册可能滞后于官方公告，转会窗口期间尤其明显
- 不含任何选手的实时数据（rating / K-D / ADR），5Eplay 只给队伍级击杀，HLTV 拒绝访问
- 所以别拿这里的名字去编数据

礼数：Liquipedia 的 API 条款要求 gzip、可识别的 UA（带联系方式）、
并且请求间隔不少于 2 秒。这三条都在 _get() 里硬编码了。
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

import aiohttp

from astrbot.api import llm_tool, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

API = "https://liquipedia.net/counterstrike/api.php"
UA_TMPL = "AstrBotCompanionPlayerCard/1.0 (AstrBot CS companion; contact: {contact})"
MIN_INTERVAL = 2.2  # Liquipedia: 不超过 1 请求 / 2 秒
TIMEOUT = 20
PLAYER_TTL = 7 * 86400
ROSTER_TTL = 12 * 3600
CACHE_MAX = 400

# 常见旗帜代码 → 中文（Liquipedia 用 ISO 3166-1 alpha-2）
FLAG = {
    "cn": "中国", "hk": "中国香港", "tw": "中国台湾", "mo": "中国澳门",
    "sk": "斯洛伐克", "pl": "波兰", "ca": "加拿大", "dk": "丹麦", "fr": "法国",
    "de": "德国", "lv": "拉脱维亚", "ru": "俄罗斯", "ua": "乌克兰", "us": "美国",
    "br": "巴西", "se": "瑞典", "fi": "芬兰", "no": "挪威", "ee": "爱沙尼亚",
    "lt": "立陶宛", "rs": "塞尔维亚", "ba": "波黑", "hr": "克罗地亚",
    "si": "斯洛文尼亚", "cz": "捷克", "hu": "匈牙利", "ro": "罗马尼亚",
    "bg": "保加利亚", "tr": "土耳其", "il": "以色列", "au": "澳大利亚",
    "nz": "新西兰", "jp": "日本", "kr": "韩国", "mn": "蒙古", "in": "印度",
    "id": "印尼", "my": "马来西亚", "th": "泰国", "vn": "越南", "ph": "菲律宾",
    "sg": "新加坡", "kz": "哈萨克斯坦", "uz": "乌兹别克斯坦", "by": "白俄罗斯",
    "md": "摩尔多瓦", "mk": "北马其顿", "me": "黑山", "al": "阿尔巴尼亚",
    "gr": "希腊", "at": "奥地利", "ch": "瑞士", "nl": "荷兰", "be": "比利时",
    "gb": "英国", "ie": "爱尔兰", "es": "西班牙", "pt": "葡萄牙", "it": "意大利",
    "ar": "阿根廷", "cl": "智利", "uy": "乌拉圭", "pe": "秘鲁", "co": "哥伦比亚",
    "mx": "墨西哥", "za": "南非", "gt": "危地马拉", "py": "巴拉圭",
    "bo": "玻利维亚", "ec": "厄瓜多尔", "ve": "委内瑞拉", "cr": "哥斯达黎加",
    "pa": "巴拿马", "do": "多米尼加", "pr": "波多黎各", "hn": "洪都拉斯",
    "eg": "埃及", "ma": "摩洛哥", "tn": "突尼斯",
    "lb": "黎巴嫩", "jo": "约旦", "sa": "沙特", "ae": "阿联酋",
}

NAME2CN = {
    "china": "中国", "hong kong": "中国香港", "taiwan": "中国台湾", "macau": "中国澳门",
    "ukraine": "乌克兰", "russia": "俄罗斯", "brazil": "巴西", "united states": "美国",
    "sweden": "瑞典", "denmark": "丹麦", "france": "法国", "germany": "德国",
    "poland": "波兰", "slovakia": "斯洛伐克", "canada": "加拿大", "latvia": "拉脱维亚",
    "estonia": "爱沙尼亚", "lithuania": "立陶宛", "finland": "芬兰", "norway": "挪威",
    "united kingdom": "英国", "england": "英国", "netherlands": "荷兰", "belgium": "比利时",
    "switzerland": "瑞士", "austria": "奥地利", "spain": "西班牙", "portugal": "葡萄牙",
    "italy": "意大利", "turkey": "土耳其", "israel": "以色列", "australia": "澳大利亚",
    "new zealand": "新西兰", "japan": "日本", "south korea": "韩国", "korea": "韩国",
    "mongolia": "蒙古", "india": "印度", "indonesia": "印尼", "malaysia": "马来西亚",
    "thailand": "泰国", "vietnam": "越南", "philippines": "菲律宾", "singapore": "新加坡",
    "kazakhstan": "哈萨克斯坦", "uzbekistan": "乌兹别克斯坦", "belarus": "白俄罗斯",
    "moldova": "摩尔多瓦", "serbia": "塞尔维亚", "bosnia and herzegovina": "波黑",
    "croatia": "克罗地亚", "slovenia": "斯洛文尼亚", "czech republic": "捷克",
    "czechia": "捷克", "hungary": "匈牙利", "romania": "罗马尼亚", "bulgaria": "保加利亚",
    "north macedonia": "北马其顿", "montenegro": "黑山", "albania": "阿尔巴尼亚",
    "greece": "希腊", "ireland": "爱尔兰", "argentina": "阿根廷", "chile": "智利",
    "uruguay": "乌拉圭", "peru": "秘鲁", "colombia": "哥伦比亚", "mexico": "墨西哥",
    "south africa": "南非", "egypt": "埃及", "morocco": "摩洛哥", "tunisia": "突尼斯",
    "lebanon": "黎巴嫩", "jordan": "约旦", "saudi arabia": "沙特",
    "united arab emirates": "阿联酋", "iraq": "伊拉克", "iran": "伊朗",
    "kosovo": "科索沃", "georgia": "格鲁吉亚", "armenia": "亚美尼亚",
    "azerbaijan": "阿塞拜疆", "kyrgyzstan": "吉尔吉斯斯坦", "mexico ": "墨西哥",
}

ROLE_CN = {
    "rifle": "步枪", "rifler": "步枪", "awp": "狙击", "awper": "狙击",
    "igl": "指挥", "entry": "突破", "entry fragger": "突破", "support": "辅助",
    "lurker": "潜伏", "coach": "教练", "analyst": "分析师",
    "loan": "租借", "stand-in": "替补", "inactive": "不活跃",
}


def cn(flag: str) -> str:
    raw = (flag or "").strip()
    if not raw:
        return ""
    low = raw.lower()
    if low in FLAG:
        return FLAG[low]
    if low in NAME2CN:
        return NAME2CN[low]
    return raw.upper() if len(raw) <= 3 else raw


def cn_role(raw: str) -> str:
    parts = [p.strip().lower() for p in re.split(r"[,/，]", raw or "") if p.strip()]
    return "、".join(ROLE_CN.get(p, p) for p in parts)


def days_since(s: str) -> int | None:
    s = (s or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return None
    try:
        return (date.today() - datetime.strptime(s, "%Y-%m-%d").date()).days
    except ValueError:
        return None


def human_days(d: int | None) -> str:
    if d is None:
        return ""
    if d < 45:
        return f"{d} 天"
    if d < 730:
        return f"{round(d / 30)} 个月"
    return f"{d / 365:.1f} 年"


def age_of(birth: str) -> int | None:
    d = days_since(birth)
    return None if d is None else int(d // 365.25)


def strip_wiki(s: str) -> str:
    s = re.sub(r"<ref[^>]*/>", "", s)
    s = re.sub(r"<ref[^>]*>.*?</ref>", "", s, flags=re.S)
    s = re.sub(r"<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[\[([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\{\{abbr\|([^|}]*)[^}]*\}\}", r"\1", s)
    s = s.replace("'''", "").replace("''", "")
    s = re.sub(r"<br\s*/?>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def brace_block(s: str, start: int) -> str:
    """从 start 处的 {{ 开始，按花括号配平取出完整模板（模板里可能套模板）。"""
    depth, j = 0, start
    while j < len(s) - 1:
        two = s[j:j + 2]
        if two == "{{":
            depth += 1
            j += 2
            continue
        if two == "}}":
            depth -= 1
            j += 2
            if depth == 0:
                return s[start:j]
            continue
        j += 1
    return s[start:]


def _kv(body: str, key: str) -> str:
    m = re.search(r"^\|\s*" + key + r"\s*=\s*(.+?)\s*$", body, flags=re.M)
    return strip_wiki(m.group(1)) if m else ""


@register(
    "astrbot_plugin_cs_player",
    "local",
    "选手簿：看比赛时认人（选手卡 / 首发名单 / 近期换人）",
    "1.0.0",
)
class CsPlayer(Star):
    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        self.config = dict(config or {})
        self._session: aiohttp.ClientSession | None = None
        self._lock = asyncio.Lock()
        self._last = 0.0
        self._cache: dict[str, dict[str, Any]] = {"player": {}, "roster": {}}
        self._path: Path | None = None
        contact = str(self.config.get("contact") or "you@example.com")
        self._ua = UA_TMPL.format(contact=contact)
        self._load()

    # ---------- 缓存 ----------
    def _load(self) -> None:
        try:
            base = Path(StarTools.get_data_dir())
            base.mkdir(parents=True, exist_ok=True)
            self._path = base / "player_cache.json"
            if self._path.exists():
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data.get("player"), dict):
                    self._cache = data
            logger.info(f"[cs_player] 缓存载入 {self._path}")
        except Exception as exc:
            self._path = None
            logger.debug(f"[cs_player] 缓存不可用: {exc}")

    def _save(self) -> None:
        if not self._path:
            return
        try:
            for bucket in self._cache.values():
                if len(bucket) > CACHE_MAX:
                    keep = sorted(bucket.items(), key=lambda kv: kv[1].get("ts", 0))[-CACHE_MAX:]
                    bucket.clear()
                    bucket.update(dict(keep))
            self._path.write_text(
                json.dumps(self._cache, ensure_ascii=False), encoding="utf-8"
            )
        except Exception as exc:
            logger.debug(f"[cs_player] 缓存写入失败: {exc}")

    def _hit(self, kind: str, key: str, ttl: int) -> str | None:
        row = self._cache.get(kind, {}).get(key)
        if not row:
            return None
        if time.time() - float(row.get("ts", 0)) > ttl:
            return None
        return row.get("text")

    def _put(self, kind: str, key: str, text: str) -> None:
        self._cache.setdefault(kind, {})[key] = {"ts": time.time(), "text": text}
        self._save()

    # ---------- 网络 ----------
    async def _sess(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=TIMEOUT),
                headers={
                    "User-Agent": self._ua,
                    "Accept-Encoding": "gzip, deflate",
                    "Accept": "application/json",
                },
            )
        return self._session

    async def _get(self, params: dict[str, Any]) -> dict[str, Any] | None:
        """全局限速：任意两次 Liquipedia 请求间隔 >= MIN_INTERVAL。"""
        async with self._lock:
            wait = MIN_INTERVAL - (time.time() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                sess = await self._sess()
                async with sess.get(API, params=params) as resp:
                    self._last = time.time()
                    if resp.status == 429:
                        logger.warning("[cs_player] Liquipedia 限流 429")
                        return None
                    if resp.status != 200:
                        logger.warning(f"[cs_player] HTTP {resp.status}")
                        return None
                    return json.loads(await resp.text())
            except Exception as exc:
                logger.warning(f"[cs_player] 请求失败: {type(exc).__name__} {exc}")
                return None
            finally:
                self._last = time.time()

    async def _wikitext(self, title: str) -> str | None:
        data = await self._get(
            {"action": "parse", "page": title, "prop": "wikitext", "format": "json",
             "redirects": "1"}
        )
        if not data or "parse" not in data:
            return None
        wt = (data["parse"].get("wikitext") or {}).get("*") or ""
        m = re.match(r"\s*#REDIRECT\s*\[\[([^\]]+)\]\]", wt, flags=re.I)
        if m:
            return await self._wikitext(m.group(1).replace(" ", "_"))
        return wt or None

    async def _search(self, text: str) -> str | None:
        data = await self._get(
            {"action": "query", "format": "json", "list": "search",
             "srsearch": text, "srlimit": "5", "srnamespace": "0"}
        )
        hits = ((data or {}).get("query") or {}).get("search") or []
        if not hits:
            return None
        # 优先标题等于输入的
        low = text.replace("_", " ").lower()
        for h in hits:
            if (h.get("title") or "").lower() == low:
                return h["title"]
        return hits[0].get("title")

    async def _page(self, raw: str, suffix: str = "") -> tuple[str, str] | None:
        """把用户给的队名/人名解析成 (标题, wikitext)。带几种常见写法回退。"""
        raw = (raw or "").strip()
        if not raw:
            return None
        tries = []
        slug = raw.replace(" ", "_")
        tries.append(slug)
        if suffix:
            tries.append(slug + "_" + suffix)
        if raw.lower().startswith("team "):
            tries.append(slug[5:])
        for t in dict.fromkeys(tries):
            wt = await self._wikitext(t)
            if wt:
                return t, wt
        title = await self._search(raw + (f" {suffix}" if suffix else ""))
        if not title:
            return None
        wt = await self._wikitext(title.replace(" ", "_"))
        return (title, wt) if wt else None

    # ---------- 解析 ----------
    @staticmethod
    def _infobox(wt: str) -> str:
        i = wt.find("{{Infobox")
        return brace_block(wt, i) if i >= 0 else ""

    def _team_history(self, box: str) -> list[tuple[str, str, str]]:
        out = []
        for m in re.finditer(r"\{\{TH\|([^}]*)\}\}", box):
            parts = [p.strip() for p in m.group(1).split("|")]
            if len(parts) < 2:
                continue
            span = strip_wiki(parts[0])
            team = strip_wiki(parts[1])
            note = strip_wiki(parts[2]) if len(parts) > 2 else ""
            if team:
                out.append((span, team, note))
        return out

    def _intro(self, wt: str, box: str) -> str:
        rest = wt.replace(box, " ", 1)
        paras = [
            p for p in rest.split("\n")
            if p.strip() and not p.strip().startswith(("|", "{{", "==", "[[Category", "[[File", "*"))
        ]
        for p in paras:
            t = strip_wiki(p)
            t = re.sub(r"\{\{[^{}]*\}\}", "", t)  # {{born}} 之类
            t = re.sub(r"\{\{[^{}]*\}\}", "", t)
            t = re.sub(r"\s+", " ", t).strip()
            if len(t) > 60:
                # 按句号收口，别把句子截成半截
                m = re.search(r"^.{80,240}?[.。](?=\s|$)", t)
                return (m.group(0) if m else t[:200] + "…").strip()
        return ""

    # ---------- 工具 1：选手卡 ----------
    @llm_tool(name="cs_player")
    async def cs_player(self, event: AstrMessageEvent, name: str) -> str:
        """查一个 CS 选手是谁：真名、国籍、年龄、位置、当前队伍、在队多久、换过哪些队。

        看比赛时遇到不认识的名字、或者想聊"这个人以前在哪"时调用。
        这里没有实时数据（rating / 击杀 / ADR），别用它编数据。

        Args:
            name(string): 选手 ID，比如 s1mple、ZywOo、donk、westmelon
        """
        raw = (name or "").strip()
        if not raw:
            return "要查谁？给我一个选手 ID。"

        hit = self._hit("player", raw.lower(), PLAYER_TTL)
        if hit:
            return hit

        found = await self._page(raw)
        if not found:
            return (
                f"Liquipedia 上没找到「{raw}」。可能是 ID 拼写不同（大小写、下划线），"
                f"也可能是新选手还没建页。别猜他的履历。"
            )
        title, wt = found
        box = self._infobox(wt)
        if not box:
            return f"「{title}」这个页面不是选手页，换一个 ID 试试。"

        pid = _kv(box, "id") or title.replace("_", " ")
        real = _kv(box, "romanized_name") or _kv(box, "name")
        flag = _kv(box, "country")
        birth = _kv(box, "birth_date")
        roles = _kv(box, "roles")
        team = _kv(box, "team")
        status = _kv(box, "status")
        since = _kv(box, "years_active")
        nicks = _kv(box, "nicknames")
        hist = self._team_history(box)
        intro = self._intro(wt, box)

        lines = [f"【{pid}】{real}" + (f"｜{cn(flag)}" if flag else "")]
        meta = []
        if birth:
            a = age_of(birth)
            meta.append(f"{birth} 生" + (f"（{a} 岁）" if a else ""))
        if roles:
            meta.append(cn_role(roles) + "位")
        if status:
            meta.append(status)
        if since:
            meta.append(f"活跃 {since}")
        if meta:
            lines.append("· " + "｜".join(meta))
        if team:
            lines.append(f"· 现在在 {team}")
        if nicks:
            lines.append(f"· 外号：{nicks}")

        if hist:
            recent = hist[-4:]
            lines.append("· 履历（近几段）：")
            for span, t, note in recent:
                extra = f"（{note}）" if note else ""
                lines.append(f"    {span} → {t}{extra}")
            lines.append(f"    （总共 {len(hist)} 段，含租借/替补）")

        if intro:
            lines.append(f"· 背景：{intro}")

        lines.append(
            "（来源 Liquipedia，页面可能滞后于官方公告。这里没有比赛数据，"
            "rating、击杀、ADR 一律不许编。）"
        )
        text = "\n".join(lines)
        self._put("player", raw.lower(), text)
        return text

    # ---------- 工具 2：首发名单 ----------
    @llm_tool(name="cs_roster")
    async def cs_roster(self, event: AstrMessageEvent, team: str) -> str:
        """查一支队现在的首发五人 + 教练：国籍、入队日期、在队多久、谁是指挥。

        比赛开打前想知道"这五个人是谁、磨合多久了"时调用。

        Args:
            team(string): 队名，比如 FaZe、Liquid、LVG、9z、BC.Game
        """
        raw = (team or "").strip()
        if not raw:
            return "要看哪支队？"

        hit = self._hit("roster", raw.lower(), ROSTER_TTL)
        if hit:
            return hit

        found = await self._page(raw, suffix="")
        if not found:
            return f"Liquipedia 上没找到「{raw}」这支队。换个写法（比如 FaZe Clan / Team Liquid）再试。"
        title, wt = found
        roster = self._parse_roster(wt)
        if not roster["active"] and not roster["former"]:
            return f"「{title}」页面里没有解析到花名册，可能是业余队或页面结构不同。"

        name = title.replace("_", " ")
        lines = [f"【{name}】当前首发"]
        acts = [p for p in roster["active"] if p.get("kind") == "player"]
        coaches = [p for p in roster["active"] if p.get("kind") == "coach"]
        for p in acts:
            lines.append("  " + self._person_line(p))
        if not acts:
            lines.append("  （首发栏是空的，可能页面还没更新）")
        for p in coaches:
            lines.append("  教练 " + self._person_line(p))
        for p in roster["inactive"]:
            if p.get("kind") == "player":
                lines.append("  [不活跃] " + self._person_line(p))

        if acts:
            days = [d for d in (days_since(p.get("joindate", "")) for p in acts) if d is not None]
            if days:
                lines.append(
                    f"  · 磨合：五人里最久 {human_days(max(days))}，最近加入 {human_days(min(days))}，"
                    f"平均 {human_days(round(sum(days) / len(days)))}"
                )

        recent = [p for p in roster["former"] if (days_since(p.get("leavedate", "")) or 9999) <= 120]
        if recent:
            lines.append("  · 近 4 个月离队：")
            for p in recent[:4]:
                to = p.get("newteam", "")
                lines.append(
                    f"      {p['id']}（{p.get('leavedate', '')}）" + (f" → {to}" if to else "")
                )

        lines.append("（来源 Liquipedia，转会窗口里可能滞后；没有选手比赛数据。）")
        text = "\n".join(lines)
        self._put("roster", raw.lower(), text)
        return text

    # ---------- 工具 3：换人记录 ----------
    @llm_tool(name="cs_lineup_change")
    async def cs_lineup_change(self, event: AstrMessageEvent, team: str, months: int = 6) -> str:
        """看一支队最近的人员进出：谁什么时候来的、谁什么时候走的、去了哪。

        想解释"这队为什么配合怪""阵容是不是刚变过"时调用。

        Args:
            team(string): 队名
            months(number): 往回看几个月，默认 6，最大 24
        """
        raw = (team or "").strip()
        if not raw:
            return "哪支队？"
        months = max(1, min(int(months or 6), 24))
        key = f"{raw.lower()}|{months}"

        found = await self._page(raw)
        if not found:
            return f"没找到「{raw}」。"
        title, wt = found
        roster = self._parse_roster(wt)
        name = title.replace("_", " ")

        joined = [p for p in roster["active"]
                  if p.get("kind") == "player"
                  and (days_since(p.get("joindate", "")) or 99999) <= months * 31]
        left = [p for p in roster["former"]
                if p.get("kind") == "player"
                and (days_since(p.get("leavedate", "")) or 99999) <= months * 31]

        lines = [f"【{name}】近 {months} 个月人员流动"]
        if joined:
            lines.append("  进：")
            for p in sorted(joined, key=lambda x: x.get("joindate") or "", reverse=True):
                lines.append("    + " + self._person_line(p))
        if left:
            lines.append("  出：")
            for p in sorted(left, key=lambda x: x.get("leavedate") or "", reverse=True)[:8]:
                to = p.get("newteam", "")
                lines.append(
                    f"    - {p['id']}（{p.get('flag', '') and cn(p['flag'])}）"
                    f"{p.get('leavedate', '')} 离队" + (f" → {to}" if to else "")
                )
        if not joined and not left:
            lines.append(f"  这 {months} 个月没有记录到变动，阵容是稳定的。")
            days = [d for d in (days_since(p.get("joindate", "")) for p in roster["active"]) if d is not None]
            if days:
                lines.append(f"  现有五人在队时长：最久 {human_days(max(days))}，最近 {human_days(min(days))}。")

        lines.append("（来源 Liquipedia，只记有据可查的进出；租借和替补都单独标了。）")
        return "\n".join(lines)

    # ---------- 花名册解析 ----------
    @staticmethod
    def _kind(p: dict[str, str]) -> str:
        """把花名册里的人分成 选手 / 教练 / 工作人员，别把 CEO 当首发。"""
        r = (p.get("role") or "").lower()
        if any(k in r for k in ("manager", "owner", "ceo", "director", "analyst",
                                "psychologist", "physio", "founder", "president")):
            return "staff"
        if "coach" in r:
            return "coach"
        return "player"

    def _parse_roster(self, wt: str) -> dict[str, list[dict[str, str]]]:
        out: dict[str, list[dict[str, str]]] = {"active": [], "inactive": [], "former": []}
        i = wt.find("==Player Roster==")
        if i < 0:
            i = wt.find("== Player Roster ==")
        seg = wt[i:] if i >= 0 else wt
        # 只取 CS2 那一栏，避免把 CS:GO 时代的老名单也算进来
        cut = seg.find("|content2=")
        if cut > 0:
            seg = seg[:cut]

        section = "active"
        for line in seg.split("\n"):
            s = line.strip()
            m = re.match(r"^===\s*(Active|Inactive|Former|Coach|Trial)\s*===$", s, re.I)
            if m:
                tag = m.group(1).lower()
                section = "active" if tag in ("active", "coach", "trial") else tag
                continue
            if "{{Person|" not in s:
                continue
            idx = s.find("{{Person|")
            block = brace_block(s, idx)
            if not block.endswith("}}"):
                continue
            body = block[len("{{Person|"):-2]
            # 先把 ref 与 abbr 拆掉，否则里面的 name= 和竖线会把字段切碎
            body = re.sub(r"<ref[^>]*/>", "", body)
            body = re.sub(r"<ref[^>]*>.*?</ref>", "", body, flags=re.S)
            body = re.sub(r"\{\{[Aa]bbr\|([^|}]*)[^}]*\}\}", r"\1", body)
            fields: dict[str, str] = {}
            for seg in body.split("|"):
                if "=" not in seg:
                    continue
                k, v = seg.split("=", 1)
                fields[k.strip().lower()] = strip_wiki(v)
            pid = fields.get("id", "").strip()
            if not pid:
                continue
            row = {
                "id": pid,
                "name": fields.get("name", ""),
                "flag": fields.get("flag", ""),
                "role": fields.get("role", ""),
                "joindate": fields.get("joindate", "")[:10],
                "leavedate": fields.get("leavedate", "")[:10],
                "newteam": fields.get("newteam", ""),
                "igl": "y" if fields.get("igl", "").lower() == "y" else "",
            }
            row["kind"] = self._kind(row)
            if section in ("active", "inactive", "former"):
                out[section].append(row)
            else:
                out["former"].append(row)
        return out

    def _person_line(self, p: dict[str, str]) -> str:
        bits = [p["id"]]
        meta = []
        if p.get("igl"):
            meta.append("指挥")
        if p.get("role"):
            r = cn_role(p["role"])
            if r and r != "教练":
                meta.append(r)
        if p.get("flag"):
            meta.append(cn(p["flag"]))
        if meta:
            bits.append("（" + "·".join(meta) + "）")
        if p.get("name"):
            bits.append(f" {p['name']}")
        d = days_since(p.get("joindate", ""))
        if d is not None:
            bits.append(f" — 入队 {p['joindate']}（{human_days(d)}）")
        elif p.get("joindate"):
            bits.append(f" — 入队 {p['joindate']}")
        if p.get("role", "").lower() in ("loan", "stand-in"):
            bits.append(" [租借/临时]")
        return "".join(bits)

    # ---------- 工具 4：赛前对位 ----------
    async def _roster_data(self, team: str) -> dict[str, Any] | None:
        found = await self._page((team or "").strip())
        if not found:
            return None
        title, wt = found
        r = self._parse_roster(wt)
        acts = [p for p in r["active"] if p.get("kind") == "player"]
        coach = next((p for p in r["active"] if p.get("kind") == "coach"), None)
        days = [d for d in (days_since(p.get("joindate", "")) for p in acts) if d is not None]
        leaves = [
            p for p in r["former"]
            if p.get("kind") == "player"
            and (days_since(p.get("leavedate", "")) or 9999) <= 120
        ]
        return {
            "name": title.replace("_", " "),
            "players": acts,
            "coach": coach,
            "avg": round(sum(days) / len(days)) if days else None,
            "min": min(days) if days else None,
            "max": max(days) if days else None,
            "leaves": leaves,
        }

    @staticmethod
    def _brief_line(d: dict[str, Any]) -> list[str]:
        out = [f"【{d['name']}】"]
        names = []
        for p in d["players"]:
            tag = []
            if p.get("igl"):
                tag.append("指挥")
            elif p.get("role"):
                tag.append(cn_role(p["role"]))
            if p.get("flag"):
                tag.append(cn(p["flag"]))
            names.append(p["id"] + (f"({'·'.join(tag)})" if tag else ""))
        out.append("  " + "、".join(names) if names else "  （名单为空）")
        bits = []
        if d["avg"] is not None:
            bits.append(f"平均在队 {human_days(d['avg'])}")
        if d["max"] is not None:
            bits.append(f"最久 {human_days(d['max'])}")
        if d["min"] is not None:
            bits.append(f"最近加入 {human_days(d['min'])}")
        if bits:
            out.append("  · " + "，".join(bits))
        if d["coach"]:
            c = d["coach"]
            out.append(f"  · 教练 {c['id']}（{cn(c.get('flag', ''))}）")
        if d["leaves"]:
            who = "、".join(
                p["id"] + (f"→{p['newteam']}" if p.get("newteam") else "")
                for p in d["leaves"][:3]
            )
            out.append(f"  · 近 4 个月离队 {len(d['leaves'])} 人：{who}")
        return out

    @llm_tool(name="cs_prematch")
    async def cs_prematch(self, event: AstrMessageEvent, team1: str, team2: str) -> str:
        """开打前一次看两队阵容：各自五人、指挥、教练、在队时长、近期换人。

        想说"谁更默契""这队是不是刚换人"时调用。它只给名单与时长，不给实力判断。

        Args:
            team1(string): 第一支队
            team2(string): 第二支队
        """
        a = None
        b = None
        for t, slot in ((team1, "a"), (team2, "b")):
            d = await self._roster_data(t)
            if slot == "a":
                a = d
            else:
                b = d
        if not a and not b:
            return f"「{team1}」「{team2}」都没查到，换个写法（FaZe Clan / Team Liquid）再试。"

        lines = [f"【赛前对位】{(a or {}).get('name', team1)} vs {(b or {}).get('name', team2)}"]
        for d in (a, b):
            if d:
                lines.extend(self._brief_line(d))
            else:
                lines.append("  （另一支队没解析到花名册）")

        if a and b and a.get("avg") and b.get("avg"):
            diff = a["avg"] - b["avg"]
            if abs(diff) < 120:
                lines.append("· 默契：两队平均在队时长接近，都是成型阵容。")
            else:
                older, newer = (a, b) if diff > 0 else (b, a)
                lines.append(
                    f"· 默契：{older['name']} 平均 {human_days(older['avg'])}，"
                    f"{newer['name']} 平均 {human_days(newer['avg'])}，后者的阵容更新。"
                )
        lines.append(
            "（名单与在队时长来自 Liquipedia。这是履历，不是实力判断——"
            "别拿它推谁赢，也别编选手的 rating 或数据。）"
        )
        return "\n".join(lines)

    # ---------- 指令 ----------
    @filter.command("player")
    async def player_cmd(self, event: AstrMessageEvent, arg: str = ""):
        """快捷查人：/player s1mple"""
        try:
            yield event.plain_result(await self.cs_player(event, arg.strip() or "s1mple"))
        except Exception as exc:
            yield event.plain_result(f"查不到：{type(exc).__name__}")

    @filter.command("roster")
    async def roster_cmd(self, event: AstrMessageEvent, arg: str = ""):
        """快捷看阵容：/roster FaZe"""
        try:
            yield event.plain_result(await self.cs_roster(event, arg.strip() or "FaZe"))
        except Exception as exc:
            yield event.plain_result(f"查不到：{type(exc).__name__}")

    async def terminate(self):
        if self._session and not self._session.closed:
            await self._session.close()
