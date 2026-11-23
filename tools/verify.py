"""注册验证：确认合并版与六个独立版对模型暴露的能力完全一致。

它检查的是三件容易「看起来对、其实没生效」的事：

1. **handler 是否真的挂在主模块上**。装饰器一注册就写进全局 registry，
   并记下定义处的模块路径；AstrBot 按这个路径精确匹配来绑定插件实例。
   子模块里的 handler 路径对不上，等于没注册——这里直接查 registry。
2. **工具数量与名字**。22 个，一个不多一个不少。
3. **JSON Schema 是否逐字段一致**。`@llm_tool` 从签名和 docstring 生成给模型
   看的参数说明，转发壳是逐字复制的，这里用旧的六个插件做基准比对。

用法（必须用 AstrBot 运行环境里的解释器，它才有 astrbot 包）：
    python tools/verify.py new    # 合并版
    python tools/verify.py old    # 六个独立版（基准）
    python tools/verify.py diff   # 比对两份结果
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OLD = ROOT / "plugins"
OUT = Path("/tmp/cs_companion_schema")

OLD_MODULES = [
    "astrbot_plugin_cs_alert",
    "astrbot_plugin_cs_bet",
    "astrbot_plugin_cs_player",
    "astrbot_plugin_cs_review",
    "astrbot_plugin_cs_schedule",
    "astrbot_plugin_cs_watchparty",
]


def collect(label: str) -> None:
    from astrbot.core.provider.register import llm_tools
    from astrbot.core.star.star import star_map
    from astrbot.core.star.star_handler import star_handlers_registry

    schemas = {}
    modules = set()
    for tool in llm_tools.func_list:
        schemas[tool.name] = tool.parameters
        modules.add(tool.handler_module_path)
    # 有的版本把工具包在 wrapper 里
    for tool in llm_tools.func_list:
        for inner in getattr(tool, "tools", []) or []:
            schemas.setdefault(inner.name, inner.parameters)

    handlers = [
        {
            "name": md.handler_name,
            "module": md.handler_module_path,
            "event": str(md.event_type),
            "filters": [type(f).__name__ for f in md.event_filters],
        }
        for md in star_handlers_registry
    ]

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{label}.json").write_text(
        json.dumps(
            {
                "label": label,
                "tools": schemas,
                "handlers": handlers,
                "star_map": list(star_map.keys()),
                "plugin_modules": sorted(modules),
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"[{label}] 工具 {len(schemas)} 个 / handler {len(handlers)} 条")
    print(f"[{label}] 插件模块: {sorted(modules)}")


def run_new() -> None:
    sys.path.insert(0, str(ROOT.parent))
    import astrbot_plugin_cs_companion.main  # noqa: F401

    collect("new")


def run_old() -> None:
    sys.path.insert(0, str(OLD))
    import importlib

    for name in OLD_MODULES:
        importlib.import_module(f"{name}.main")
    collect("old")


def run_diff() -> None:
    new = json.loads((OUT / "new.json").read_text(encoding="utf-8"))
    old = json.loads((OUT / "old.json").read_text(encoding="utf-8"))

    nt, ot = set(new["tools"]), set(old["tools"])
    print(f"工具：新 {len(nt)} / 旧 {len(ot)}")
    print("  只在新版:", sorted(nt - ot) or "无")
    print("  只在旧版:", sorted(ot - nt) or "无")

    mismatched = [
        name for name in sorted(nt & ot) if new["tools"][name] != old["tools"][name]
    ]
    print("  schema 不一致:", mismatched or "无，逐字段一致 ✓")
    for name in mismatched[:3]:
        print(f"    --- {name} 新 ---")
        print(json.dumps(new["tools"][name], ensure_ascii=False)[:400])
        print(f"    --- {name} 旧 ---")
        print(json.dumps(old["tools"][name], ensure_ascii=False)[:400])

    nh = {h["name"] for h in new["handlers"]}
    oh = {h["name"] for h in old["handlers"]}
    print(f"\nhandler：新 {len(nh)} / 旧 {len(oh)}")
    print("  只在新版:", sorted(nh - oh) or "无")
    print("  只在旧版:", sorted(oh - nh) or "无")

    bad = [
        h["module"]
        for h in new["handlers"]
        if h["module"] != "astrbot_plugin_cs_companion.main"
    ]
    print("  module 路径不正确的 handler 数:", len(bad) or "0 ✓")
    for module in sorted(set(bad))[:5]:
        print("    ", module)

    print("\nstar_map（新版）:", new["star_map"])


def main() -> None:
    action = sys.argv[1] if len(sys.argv) > 1 else "new"
    if action == "new":
        run_new()
    elif action == "old":
        run_old()
    elif action == "diff":
        run_diff()
    else:
        raise SystemExit("用法: verify.py [new|old|diff]")


if __name__ == "__main__":
    main()
