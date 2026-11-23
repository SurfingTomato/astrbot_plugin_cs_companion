"""生成 main.py：把六个核心类的 30 个 handler 变成主类的转发壳。

为什么需要转发壳（而不是直接继承或 import）：
    AstrBot 的 star_manager 在绑定插件实例时用
        star_handlers_registry.get_handlers_by_module_name(metadata.module_path)
    精确匹配 **定义 handler 的模块路径**。装饰器在 import 时就把 handler 记进
    全局 registry，并把当时的 `handler.__module__` 存成 `handler_module_path`。
    子模块里定义的 handler 路径是 `...companion.cores.alert`，永远不等于插件的
    `...companion.main`，于是既不会被绑定、也不会被激活——装饰器等于白写。

    同理，一个模块里 import 多个 Star 子类也没用：star_map 以 `cls.__module__`
    为键，同模块只会保留第一个。

    所以：装饰器全部集中在 main.py（模块路径正确），实现全部留在 cores/（可读）。
    壳里的签名和 docstring 是**逐字复制**的——`@llm_tool` 从签名和 docstring 生成
    工具的 JSON Schema，改一个字符都会让模型看到的参数说明变样。
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "plugins"
TARGET = ROOT / "main.py"

MODULES = [
    ("alert", "CsAlert", "alert", "开赛雷达"),
    ("bet", "CsBet", "bet", "打赌台"),
    ("player", "CsPlayer", "player", "选手簿"),
    ("review", "CsReview", "review", "复盘台"),
    ("schedule", "CsSchedule", "schedule", "赛程簿"),
    ("watchparty", "CsWatchParty", "watchparty", "陪看台"),
]

REGISTERED = {"llm_tool", "command", "on_llm_request"}

TEMPLATE = '''"""CS 赛事陪看套件：一个插件，六组能力，__TOTAL__ 个 handler。

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

__IMPORTS__


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
__INITS__
        self._cores = [__CORES__]

    async def terminate(self):
        """卸载时把六个核心的 aiohttp 会话与后台任务都收掉。"""
        for core in self._cores:
            try:
                await core.terminate()
            except Exception:
                pass

__BLOCKS__
'''


def dec_name(node: ast.expr) -> str:
    if isinstance(node, ast.Call):
        node = node.func
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def shell(core_attr: str, source_lines: list[str]) -> tuple[str, int]:
    tree = ast.parse("".join(source_lines))
    cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name.startswith("Cs")
    )
    out: list[str] = []
    count = 0

    for fn in cls.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        names = {dec_name(d).split(".")[-1] for d in fn.decorator_list}
        if not names & REGISTERED:
            continue

        first = min(d.lineno for d in fn.decorator_list)
        body0 = fn.body[0]
        has_doc = (
            isinstance(body0, ast.Expr)
            and isinstance(body0.value, ast.Constant)
            and isinstance(body0.value.value, str)
        )
        last = body0.end_lineno if has_doc else body0.lineno - 1

        out.extend(line.rstrip("\n") for line in source_lines[first - 1 : last])

        args = fn.args
        call: list[str] = []
        for arg in list(args.posonlyargs) + list(args.args):
            if arg.arg == "self":
                continue
            call.append(arg.arg)
        for arg in args.kwonlyargs:
            call.append(f"{arg.arg}={arg.arg}")
        if args.vararg:
            call.append(f"*{args.vararg.arg}")
        if args.kwarg:
            call.append(f"**{args.kwarg.arg}")
        joined = ", ".join(call)

        is_gen = any(
            isinstance(node, (ast.Yield, ast.YieldFrom)) for node in ast.walk(fn)
        )
        target = f"self._{core_attr}.{fn.name}"
        returns_none = fn.returns is not None and ast.unparse(fn.returns) == "None"
        if is_gen:
            out.append(f"        async for __item in {target}({joined}):")
            out.append("            yield __item")
        elif returns_none:
            out.append(f"        await {target}({joined})")
        else:
            out.append(f"        return await {target}({joined})")
        out.append("")
        count += 1

    return "\n".join(out), count


def main() -> None:
    blocks: list[str] = []
    total = 0
    for module, _cls, attr, label in MODULES:
        path = PLUGINS / f"astrbot_plugin_cs_{module}" / "main.py"
        text, n = shell(attr, path.read_text(encoding="utf-8").splitlines(True))
        total += n
        blocks.append(f"    # ---------- {label}（{n} 个） ----------\n\n{text}")

    imports = "\n".join(
        f"from .cores.{module} import {cls}" for module, cls, _a, _l in MODULES
    )
    inits = "\n".join(
        '        self._{attr} = {cls}(context, cfg.get("{attr}") or {{}})'.format(
            attr=attr, cls=cls
        )
        for _m, cls, attr, _l in MODULES
    )
    cores = ", ".join(f"self._{attr}" for _m, _c, attr, _l in MODULES)

    body = (
        TEMPLATE.replace("__TOTAL__", str(total))
        .replace("__IMPORTS__", imports)
        .replace("__INITS__", inits)
        .replace("__CORES__", cores)
        .replace("__BLOCKS__", "\n".join(blocks).rstrip())
    )
    TARGET.write_text(body, encoding="utf-8")
    print(f"main.py 已生成，共 {total} 个转发壳")


if __name__ == "__main__":
    main()
