"""CS 赛事陪看套件：一个插件，六组能力，30 个 handler。

她是「陪看台 / 打赌台 / 开赛雷达 / 赛程簿 / 选手簿 / 复盘台」六个插件的合并版。
合并不是为了省事，是因为 AstrBot 插件市场按「一个仓库 = 一个插件」收录，
分开发六个仓库会让用户装六次、配六处，也不利于发现。

数据只来自 5Eplay 与 Liquipedia，查不到就说查不到，不猜。

结构说明（改代码前请读）：
    - 本文件**只放转发壳**，带装饰器；实现全在 cores/ 下。
    - 装饰器不能挪到 cores/ 里：AstrBot 按 handler 定义处的模块路径绑定实例，
      子模块里的 handler 不会被激活。详见 tools/make_main.py 的注释。
    - 壳的签名和 docstring 是从原插件逐字复制的，`@llm_tool` 靠它们生成
      给模型看的 JSON Schema，别手改。
"""

from __future__ import annotations

from astrbot.api import llm_tool
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .cores.alert import CsAlert
from .cores.bet import CsBet
from .cores.player import CsPlayer
from .cores.review import CsReview
from .cores.schedule import CsSchedule
from .cores.watchparty import CsWatchParty


class CsCompanion(Star):
    """把一场 CS 比赛变成「你们俩一起在看」的状态。

    六个能力：
        陪看台  锁定正在看的比赛，每轮把真实比分注入上下文，图间她先开口
        打赌台  用真实赔率开盘，赛后按真实赛果结算，记连胜与彩头
        开赛雷达 盯赛程，开赛/开打/图结束/赛点/结束主动提醒
        赛程簿  未来赛程、历史战绩统计、按瑞士轮规则推演
        选手簿  选手卡、首发五人加教练、近期换人
        复盘台  逐人数据，按写死的判据指出扛把子 / 悲情英雄 / 背锅位
    """

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context)
        cfg = dict(config or {})
        # 六组配置各走一段，键名互不干扰；缺省时传空字典，核心类自己有默认值。
        self._alert = CsAlert(context, cfg.get("alert") or {})
        self._bet = CsBet(context, cfg.get("bet") or {})
        self._player = CsPlayer(context, cfg.get("player") or {})
        self._review = CsReview(context, cfg.get("review") or {})
        self._schedule = CsSchedule(context, cfg.get("schedule") or {})
        self._watchparty = CsWatchParty(context, cfg.get("watchparty") or {})
        self._cores = [self._alert, self._bet, self._player, self._review, self._schedule, self._watchparty]

    async def terminate(self):
        """卸载时把六个核心的 aiohttp 会话与后台任务都收掉。"""
        for core in self._cores:
            try:
                await core.terminate()
            except Exception:
                pass

    # ---------- 开赛雷达（3 个） ----------

    @llm_tool(name="cs_alert_recent")
    async def cs_alert_recent(self, event: AstrMessageEvent, limit: int = 5) -> str:
        """看开赛雷达最近捕获的比赛动静（开打/图结束/赛点/结果）。

        当用户问"刚才有什么动静"、"有比赛吗"、"打到哪了"，或者你想自然地提起
        刚刚发生的赛况时调用。如果这里没有内容，就别说有比赛在打。

        Args:
            limit(number): 返回最近几条，默认 5，最大 15
        """
        return await self._alert.cs_alert_recent(event, limit)

    @filter.command("csalert")
    async def csalert_cmd(self, event: AstrMessageEvent, arg: str = ""):
        """看雷达状态：/csalert"""
        async for __item in self._alert.csalert_cmd(event, arg):
            yield __item

    @llm_tool(name="cs_major_today")
    async def cs_major_today(self, event: AstrMessageEvent, limit: int = 5, kind: str = "worth") -> str:
        """挑今天（含明天凌晨）最值得看的几场，带理由：悬念最大 / 中国队伍 / 已开打。

        当用户问"今天看哪场"、"有什么好看的"、"明天几点开始"，或者你想主动推荐
        "晚上看这场"时调用。返回的赔率与支持率都是真实数据。

        Args:
            limit(number): 返回几场，默认 5，最大 10
            kind(string): worth=按悬念排序（默认）；live=只看正在打的；cn=只看有中国队伍的
        """
        return await self._alert.cs_major_today(event, limit, kind)

    # ---------- 打赌台（6 个） ----------

    @llm_tool(name="cs_bet_open")
    async def cs_bet_open(self, event: AstrMessageEvent, match: str = "") -> str:
        """开一个盘口：拿真实赛前赔率生成押注题目，等双方各押一注。

        用户说"赌一把"、"押一注"、"我们打个赌"，或者比赛快开始时你想主动开盘，调用本工具。
        开完盘要让用户先押；她自己也押，然后才有意思。

        Args:
            match(string): 队名或赛事名，留空=最近一场未开始的比赛
        """
        return await self._bet.cs_bet_open(event, match)

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
        return await self._bet.cs_bet_place(event, who, winner, series, ot)

    @llm_tool(name="cs_bet_settle")
    async def cs_bet_settle(self, event: AstrMessageEvent) -> str:
        """结算所有开着的盘口，拿真实赛果算分，更新战绩与连胜。

        比赛结束后（用户问"谁赢了/我赢没赢/结算一下"、或她发现打完了）调用。
        返回的账本可以直接用，但要用她自己的语气念，别照抄。
        """
        return await self._bet.cs_bet_settle(event)

    @llm_tool(name="cs_bet_board")
    async def cs_bet_board(self, event: AstrMessageEvent) -> str:
        """看打赌战绩：累计得分、场数、连胜、欠着的彩头。

        用户问"我赢几把了/谁厉害/账本呢"，或者她想挤兑用户时调用。
        """
        return await self._bet.cs_bet_board(event)

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
        return await self._bet.cs_bet_forfeit(event, text, owed_by, done)

    @filter.command("bet")
    async def bet_cmd(self, event: AstrMessageEvent, arg: str = ""):
        """看账本：/bet"""
        async for __item in self._bet.bet_cmd(event, arg):
            yield __item

    # ---------- 选手簿（6 个） ----------

    @llm_tool(name="cs_player")
    async def cs_player(self, event: AstrMessageEvent, name: str) -> str:
        """查一个 CS 选手是谁：真名、国籍、年龄、位置、当前队伍、在队多久、换过哪些队。

        看比赛时遇到不认识的名字、或者想聊"这个人以前在哪"时调用。
        这里没有实时数据（rating / 击杀 / ADR），别用它编数据。

        Args:
            name(string): 选手 ID，比如 s1mple、ZywOo、donk、westmelon
        """
        return await self._player.cs_player(event, name)

    @llm_tool(name="cs_roster")
    async def cs_roster(self, event: AstrMessageEvent, team: str) -> str:
        """查一支队现在的首发五人 + 教练：国籍、入队日期、在队多久、谁是指挥。

        比赛开打前想知道"这五个人是谁、磨合多久了"时调用。

        Args:
            team(string): 队名，比如 FaZe、Liquid、LVG、9z、BC.Game
        """
        return await self._player.cs_roster(event, team)

    @llm_tool(name="cs_lineup_change")
    async def cs_lineup_change(self, event: AstrMessageEvent, team: str, months: int = 6) -> str:
        """看一支队最近的人员进出：谁什么时候来的、谁什么时候走的、去了哪。

        想解释"这队为什么配合怪""阵容是不是刚变过"时调用。

        Args:
            team(string): 队名
            months(number): 往回看几个月，默认 6，最大 24
        """
        return await self._player.cs_lineup_change(event, team, months)

    @llm_tool(name="cs_prematch")
    async def cs_prematch(self, event: AstrMessageEvent, team1: str, team2: str) -> str:
        """开打前一次看两队阵容：各自五人、指挥、教练、在队时长、近期换人。

        想说"谁更默契""这队是不是刚换人"时调用。它只给名单与时长，不给实力判断。

        Args:
            team1(string): 第一支队
            team2(string): 第二支队
        """
        return await self._player.cs_prematch(event, team1, team2)

    @filter.command("player")
    async def player_cmd(self, event: AstrMessageEvent, arg: str = ""):
        """快捷查人：/player s1mple"""
        async for __item in self._player.player_cmd(event, arg):
            yield __item

    @filter.command("roster")
    async def roster_cmd(self, event: AstrMessageEvent, arg: str = ""):
        """快捷看阵容：/roster FaZe"""
        async for __item in self._player.roster_cmd(event, arg):
            yield __item

    # ---------- 复盘台（3 个） ----------

    @llm_tool(name="cs_review")
    async def cs_review(self, event: AstrMessageEvent, team: str = "") -> str:
        """赛后复盘：逐人 K-D-A / ADR / KAST / 评分，并判定谁扛、谁是悲情英雄、谁背锅。

        比赛打完后用。不传队名就取最近结束的一场。

        Args:
            team(string): 队名关键词，留空取最近一场已结束的比赛
        """
        return await self._review.cs_review(event, team)

    @llm_tool(name="cs_match_stats")
    async def cs_match_stats(self, event: AstrMessageEvent, team: str = "") -> str:
        """只要逐人数据表，不下判断。想自己解读或只查某个人的数据时用。

        Args:
            team(string): 队名关键词，留空取最近一场已结束的比赛
        """
        return await self._review.cs_match_stats(event, team)

    @filter.command("review")
    async def review_cmd(self, event: AstrMessageEvent, arg: str = ""):
        """快捷复盘：/review 或 /review FaZe"""
        async for __item in self._review.review_cmd(event, arg):
            yield __item

    # ---------- 赛程簿（5 个） ----------

    @llm_tool(name="cs_schedule")
    async def cs_schedule(self, event: AstrMessageEvent, days: int = 3, team: str = "", event_name: str = "") -> str:
        """看未来赛程（今天起若干天内），含未定对阵的占位场次。

        用户问"明天几点开始""接下来有什么""这届赛程怎么排"时调用。
        瑞士轮后续轮次在赛前只显示 TBD vs TBD，这是正常的占位，不要说成大问题。

        Args:
            days(number): 看几天，默认 3，最大 14
            team(string): 只看某队，可留空
            event_name(string): 赛事名关键词，默认自动找最近的 Major
        """
        return await self._schedule.cs_schedule(event, days, team, event_name)

    @llm_tool(name="cs_history")
    async def cs_history(self, event: AstrMessageEvent, team: str, days: int = 12, limit: int = 8) -> str:
        """查某队最近的历史赛果与真实统计（地图胜率、回合差、场均击杀、KD、加时次数）。

        用户问"他们最近状态怎么样""赢了几场""对上谁吃亏"时调用。
        统计只用打完的比赛算，没打完的不会混进去。

        Args:
            team(string): 队名，例如 "FaZe"、"Liquid"
            days(number): 往前查几天，默认 12，最大 30（查得越久越慢）
            limit(number): 最多列几场赛果，默认 8
        """
        return await self._schedule.cs_history(event, team, days, limit)

    @llm_tool(name="cs_swiss")
    async def cs_swiss(self, event: AstrMessageEvent, event_name: str = "") -> str:
        """推演瑞士轮：当前积分榜 + Buchholz + 下一轮可能的配对。

        用户问"现在谁出线了""下一轮打谁""瑞士轮怎么算"时调用。
        配对是**按规则推算的**，官方赛前只放 TBD 占位，所以必须说明这是推算。

        Args:
            event_name(string): 赛事名关键词，默认最近的 Major
        """
        return await self._schedule.cs_swiss(event, event_name)

    @llm_tool(name="cs_predict")
    async def cs_predict(self, event: AstrMessageEvent, team_a: str, team_b: str) -> str:
        """估算两队交手胜率，并给出依据（赔率优先，其次近期战绩与回合差）。

        用户问"谁会赢""你押谁"时调用。结果只是估算，必须带上不确定性说。

        Args:
            team_a(string): 一队
            team_b(string): 另一队
        """
        return await self._schedule.cs_predict(event, team_a, team_b)

    @filter.command("sched")
    async def sched_cmd(self, event: AstrMessageEvent, arg: str = ""):
        """快捷看赛程：/sched 或 /sched FaZe"""
        async for __item in self._schedule.sched_cmd(event, arg):
            yield __item

    # ---------- 陪看台（7 个） ----------

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
        return await self._watchparty.cs_watch_start(event, match, side, stance)

    @llm_tool(name="cs_watch_now")
    async def cs_watch_now(self, event: AstrMessageEvent, detail: bool = True) -> str:
        """看当前陪看的那场实时打到哪了（含地图逐图比分、赛点、她的立场和她记过的判断）。

        用户问"现在几比几/打到哪了/这张图怎么样/还有戏吗"时调用。
        返回的是真实数据，可以直接用。

        Args:
            detail(bool): true=带逐图比分，false=只要大分
        """
        return await self._watchparty.cs_watch_now(event, detail)

    @llm_tool(name="cs_watch_note")
    async def cs_watch_note(self, event: AstrMessageEvent, text: str, kind: str = "判断") -> str:
        """把她在看比赛时冒出的念头记下来，之后会被带进上下文（相当于她的看球手记）。

        当她说出一个判断、吐槽、心疼某队、临场改口、或者跟你打赌时调用。
        这些记录会在后面自动出现，让她显得"记得自己刚才说过什么"。

        Args:
            text(string): 她说的话，尽量保留原话，不要改写
            kind(string): 判断 / 吐槽 / 心疼 / 改口 / 打赌
        """
        return await self._watchparty.cs_watch_note(event, text, kind)

    @llm_tool(name="cs_watch_end")
    async def cs_watch_end(self, event: AstrMessageEvent, verdict: str = "") -> str:
        """结束陪看，生成一份小结存进历史（比分、她的立场、她记的所有判断）。

        用户说"看完了"、"不看了"、"睡了"、比赛结束后聊完时调用。
        工具会返回小结素材，她用自己语气说一遍即可，不要照读。

        Args:
            verdict(string): 她的一句话总结/情绪，可选
        """
        return await self._watchparty.cs_watch_end(event, verdict)

    @llm_tool(name="cs_gap_talk")
    async def cs_gap_talk(self, event: AstrMessageEvent) -> str:
        """图与图之间没话说时，从真实比分和固定卡池里取几条可抛的话头。

        当比赛在两图之间、暂停、或者用户沉默了一阵，调用它拿素材。
        素材里的比分是真的，卡池里的常识是真的；除此之外不要自己补数字。
        """
        return await self._watchparty.cs_gap_talk(event)

    @filter.on_llm_request()
    async def inject_watch_context(self, event: AstrMessageEvent, req=None) -> None:
        """每轮把当前比分塞进 system prompt —— 这是"在场感"的全部来源。"""
        await self._watchparty.inject_watch_context(event, req)

    @filter.command("watch")
    async def watch_cmd(self, event: AstrMessageEvent, arg: str = ""):
        """查当前陪看状态：/watch"""
        async for __item in self._watchparty.watch_cmd(event, arg):
            yield __item
