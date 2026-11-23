"""把六个独立插件改造成「核心类」：去掉 Star 基类与所有注册装饰器。

为什么不能简单 import 子模块：
    AstrBot 的 star_manager 用 `get_handlers_by_module_name(metadata.module_path)`
    精确匹配来绑定插件实例。装饰器注册到全局 registry 时记下的是定义处的
    模块路径，子模块里的 handler 永远匹配不上主模块，等于没注册。
    所以子模块里必须**没有**装饰器，装饰器只留在 main.py 的转发壳上。

这个脚本负责的机械变换：
    1. 删掉 @register(...) 类装饰器
    2. class CsXxx(Star) -> class CsXxxCore
    3. 删掉类内所有 @llm_tool(...) / @filter.command(...) 装饰器
    4. super().__init__(context) -> self.context = context
    5. 清理不再使用的 import（Star / register / llm_tool / filter）
    6. StarTools.get_data_dir() 显式传插件名（核心类不在 star_map 里，
       靠调用栈解析会抛 RuntimeError）
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

PLUGIN_NAME = "astrbot_plugin_cs_companion"
ROOT = Path(__file__).resolve().parent.parent
CORES = ROOT / "cores"

MODULES = ["alert", "bet", "player", "review", "schedule", "watchparty"]


def decorator_name(node: ast.expr) -> str:
    """`@filter.command("x")` -> 'command'；`@register(...)` -> 'register'。"""
    if isinstance(node, ast.Call):
        node = node.func
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def drop_lines(lines: list[str], spans: list[tuple[int, int]]) -> list[str]:
    """按 1-based 行号区间删除（保留末尾换行）。"""
    dead = set()
    for start, end in spans:
        dead.update(range(start, end + 1))
    return [line for idx, line in enumerate(lines, start=1) if idx not in dead]


def process(name: str) -> str:
    path = CORES / f"{name}.py"
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)

    spans: list[tuple[int, int]] = []
    class_edits: list[tuple[int, str]] = []
    cls = next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name.startswith("Cs")
    )

    # 1) 类装饰器
    for dec in cls.decorator_list:
        spans.append((dec.lineno, dec.end_lineno or dec.lineno))

    # 2) 类内方法装饰器（只删注册相关的）
    for node in cls.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if decorator_name(dec).split(".")[-1] in {
                "llm_tool",
                "command",
                "on_llm_request",
            }:
                spans.append((dec.lineno, dec.end_lineno or dec.lineno))

    # 3) 基类替换
    class_edits.append(
        (cls.lineno, re.sub(r"\(Star\)", ":", lines[cls.lineno - 1], count=1)),
    )

    body = drop_lines(lines, spans)
    # drop_lines 之后行号变了，类声明那行要重算
    for idx, line in enumerate(body, start=1):
        if line.lstrip().startswith(f"class {cls.name}("):
            body[idx - 1] = re.sub(r"\(Star\)", "", line, count=1)
            if not body[idx - 1].rstrip().endswith(":"):
                body[idx - 1] = body[idx - 1].rstrip() + ":\n"
            break

    text = "".join(body)

    # 4) super().__init__(context) -> 显式存 context
    text = re.sub(
        r"(\n\s*)super\(\)\.__init__\(context\)",
        r"\1self.context = context",
        text,
    )

    # 5) import 清理：核心类不再需要 Star / register / llm_tool / filter
    text = text.replace(
        "from astrbot.api import llm_tool, logger", "from astrbot.api import logger"
    )
    text = text.replace(
        "from astrbot.api.event import AstrMessageEvent, MessageChain, filter",
        "from astrbot.api.event import AstrMessageEvent, MessageChain",
    )
    text = text.replace(
        "from astrbot.api.event import AstrMessageEvent, filter",
        "from astrbot.api.event import AstrMessageEvent",
    )
    text = text.replace(
        "from astrbot.api.star import Context, Star, StarTools, register",
        "from astrbot.api.star import Context, StarTools",
    )
    text = text.replace(
        "from astrbot.api.star import Context, Star, register",
        "from astrbot.api.star import Context",
    )
    text = text.replace("from astrbot.api.event import filter\n", "")

    # 6) 数据目录显式指定插件名
    text = text.replace(
        "StarTools.get_data_dir()", f'StarTools.get_data_dir("{PLUGIN_NAME}")'
    )

    # 7) 顶部补一句说明，避免以后有人再把装饰器加回来
    header = (
        f"# 由 tools/make_cores.py 从 plugins/astrbot_plugin_cs_{name}/main.py 生成。\n"
        "# 这里**不要**加 @register / @llm_tool / @filter.command：\n"
        "# 子模块的 handler 不会被 AstrBot 绑定到插件实例（按模块路径精确匹配），\n"
        "# 装饰器统一留在 main.py 的转发壳上。\n"
    )
    if not text.startswith("#"):
        text = header + text
    else:
        text = header + text

    path.write_text(text, encoding="utf-8")
    return text


if __name__ == "__main__":
    for module in MODULES:
        out = process(module)
        print(f"{module}.py  {len(out.splitlines())} 行")
