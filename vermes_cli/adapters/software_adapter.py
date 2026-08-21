"""SoftwareAdapter — Vermes L2 薄插槽（最小可用实现，spike 验证用）。

设计纪律（UNIVERSAL_OPERATION_LAYER_DESIGN.md §5 / §5.3）：
- 只做「挂接 + 内省 + 注册」，绝不实现任何垂直逻辑。
- 操作层（L3）由 CLI-Anything 等社区轮子提供；本类只把生成的 CLI 挂进 Vermes。
- v1 不做领域词汇表（domain_vocab）：让 LLM 纯靠 CLI --help 驱动。

真实数据源：CLI-Anything 生成的 Click CLI（带全局 `--json` 结构化输出）。
discover_tools() 内省 CLI schema → 自动注册为 Vermes 工具（tools/registry）。

L2a/L2b 接入（§15）：
- discover_tools() 顺带为每个工具派生 intent_keywords（供阶段二细选）。
- register() 顺带 build CapabilityIndex 写入进程内 CAPABILITY_REGISTRY（供阶段一粗筛）。
- invoke() 入口插 TrustGate.check()（默认 deny-unless-declared），并注入两层发现解析到的
  后端路径环境变量（FREECAD_PATH / BLENDER_PATH）兜底 CLI-Anything 的 macOS 路径 bug。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from .discovery import (
    CLI_NATIVE,
    CapabilityIndex,
    ToolSummary,
)
from .discovery_registry import CAPABILITY_REGISTRY
from .trust_gate import ALLOW, TrustGate


@dataclass
class SoftwareAdapterSpec:
    """适配器规格：描述要挂接的软件 CLI。"""

    domain: str  # "3d" | "video" | "office" | "ide" | ...
    software: str  # "freecad" | "blender" | "libreoffice" | ...
    cli_bin: str  # PATH 中的 CLI 名，如 "cli-anything-freecad"
    domain_vocab: dict = field(default_factory=dict)  # v1 强制为空（§5.3 护栏）
    backend: Optional[str] = None  # 目标软件后端（两层发现 Layer2）：freecad / blender / ...
    operation_mechanism: str = CLI_NATIVE  # cli_native / sdk_bridge / ...


@dataclass
class CLITool:
    """一个被内省出来的 CLI 子命令，映射为 Vermes 工具。"""

    name: str  # 注册进 Vermes 的工具名，如 freecad_part_fillet_3d
    subcommand: list[str]  # 透传给 CLI 的参数，如 ["part", "fillet-3d"]
    json_schema: dict  # 从 CLI --help 内省得到的入参 schema
    description: str  # 从 CLI --help 抽取
    toolset: str  # 注册所属 toolset，如 "freecad_adapter"
    operation_mechanism: str = CLI_NATIVE
    intent_keywords: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# CLI --help 解析（纯文本解析，不依赖 click 内部 API）
# ---------------------------------------------------------------------------

_OPT_RE = re.compile(r"^\s{2,}(-[\w],?\s+)?(--[\w-]+)\s+(\w+)?\s*(.*)$")
_GROUP_LINE_RE = re.compile(r"^\s{2,}([a-z0-9_-]+)\s{2,}(.+)$")


def _run_cli(cli_bin: str, args: list[str]) -> str:
    """运行 CLI 子命令的 --help，返回 stdout。失败抛异常（由调用方处理）。"""
    proc = subprocess.run(
        [cli_bin, *args, "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    return proc.stdout or proc.stderr


def _split_sections(help_text: str) -> dict[str, list[str]]:
    """把 --help 文本按 'Options:' / 'Commands:' 分段。"""
    sections: dict[str, list[str]] = {}
    current: Optional[str] = None
    for line in help_text.splitlines():
        head = line.strip().rstrip(":").lower()
        if head in ("options", "commands", "arguments"):
            current = head
            sections[current] = []
            continue
        if current is not None and line.strip():
            sections[current].append(line)
    return sections


def _parse_option(line: str) -> Optional[tuple[str, str, str]]:
    """解析 '  -r, --radius FLOAT  Fillet radius.' → (--radius, FLOAT, help)。"""
    m = re.match(r"^\s{2,}(?:-[^,]+,\s+)?(--[\w-]+)(?:\s+([A-Z_]+))?\s*(.*)$", line)
    if not m:
        return None
    return m.group(1), (m.group(2) or "string").lower(), m.group(3).strip()


def _parse_command(line: str) -> Optional[tuple[str, str]]:
    """解析 '  fillet-3d   Apply a 3D fillet to a part.' → (fillet-3d, desc)。"""
    m = re.match(r"^\s{2,}([a-z0-9_-]+)\s{2,}(.+)$", line)
    if not m:
        return None
    return m.group(1), m.group(2).strip()


_TYPE_MAP = {
    "float": "number",
    "int": "integer",
    "text": "string",
    "string": "string",
    "path": "string",
    "bool": "boolean",
    "json": "string",
}


class SoftwareAdapter:
    """薄插槽：挂接 CLI-Anything 生成的 CLI，不写垂直逻辑。"""

    def __init__(self, spec: SoftwareAdapterSpec):
        if spec.domain_vocab:
            # §5.3 护栏：v1 禁止词汇表，只允许空 dict。
            raise ValueError(
                "v1 不允许 domain_vocab（§5.3 护栏）；垂直能力由 L3 提供"
            )
        self.spec = spec
        self.toolset = f"{spec.software}_adapter"
        self._backend = None  # 两层发现解析结果（懒缓存）

    # -- 两层发现（L2 边界 case） ------------------------------------------
    def resolve_backend(self):
        """Layer1 发现 CLI 二进制 + Layer2 定位目标软件后端（含 macOS 双候选 + 环境变量兜底）。"""
        if self._backend is not None:
            return self._backend
        if self.spec.backend:
            from .discovery import BackendLocator

            self._backend = BackendLocator().locate(self.spec.backend, self.spec.cli_bin)
        return self._backend

    # -- 内省 ---------------------------------------------------------------
    def discover_tools(self) -> list[CLITool]:
        """内省 CLI schema → 自动注册 Vermes 工具。沙箱可跑（不需目标软件）。"""
        if not shutil.which(self.spec.cli_bin):
            raise FileNotFoundError(f"CLI 未安装: {self.spec.cli_bin}")
        # 两层发现：解析后端路径（macOS bug 兜底），供 invoke() 注入环境变量
        self.resolve_backend()
        tools: list[CLITool] = []
        top = _split_sections(_run_cli(self.spec.cli_bin, []))
        groups = top.get("commands", [])
        for gline in groups:
            g = _parse_command(gline)
            if not g:
                continue
            gname, gdesc = g
            sub = _split_sections(_run_cli(self.spec.cli_bin, [gname]))
            if "commands" in sub:  # 多级命令：group → subcommand
                for sline in sub["commands"]:
                    s = _parse_command(sline)
                    if not s:
                        continue
                    sname, sdesc = s
                    opts = sub.get("options", [])
                    schema = self._build_schema(opts)
                    tool = CLITool(
                        name=f"{self.spec.software}_{gname}_{sname}",
                        subcommand=[gname, sname],
                        json_schema=schema,
                        description=sdesc,
                        toolset=self.toolset,
                        operation_mechanism=self.spec.operation_mechanism,
                        intent_keywords=self._derive_tool_keywords(gname, sname, sdesc),
                    )
                    tools.append(tool)
            else:  # 叶子命令（本身带 options）
                opts = sub.get("options", [])
                schema = self._build_schema(opts)
                tool = CLITool(
                    name=f"{self.spec.software}_{gname}",
                    subcommand=[gname],
                    json_schema=schema,
                    description=gdesc,
                    toolset=self.toolset,
                    operation_mechanism=self.spec.operation_mechanism,
                    intent_keywords=self._derive_tool_keywords(gname, "", gdesc),
                )
                tools.append(tool)
        return tools

    @staticmethod
    def _derive_tool_keywords(gname: str, sname: str, desc: str) -> list[str]:
        """从 name/subcommand/description 派生意图关键词（供阶段二细选倒排）。"""
        raw = f"{gname} {sname} {desc}".lower()
        import re as _re

        toks = _re.findall(r"[a-z0-9_]+", raw)
        stop = {"the", "a", "an", "to", "of", "and", "or", "in", "on", "for", "with", "apply", "add"}
        seen: list[str] = []
        for t in toks:
            if t in stop or len(t) < 2:
                continue
            if t not in seen:
                seen.append(t)
        return seen

    def build_capability_index(self, tools: list[CLITool]) -> CapabilityIndex:
        """discover 结果的 toolset 级能力索引（阶段一粗筛源）。"""
        summaries = [
            ToolSummary(
                name=t.name,
                description=t.description,
                subcommand=t.subcommand,
                toolset=t.toolset,
                operation_mechanism=t.operation_mechanism,
                intent_keywords=t.intent_keywords,
            )
            for t in tools
        ]
        keywords: set[str] = set()
        for ts in summaries:
            keywords.update(ts.intent_keywords)
        keywords.update([self.spec.domain, self.spec.software])
        return CapabilityIndex(
            toolset=self.toolset,
            domain=self.spec.domain,
            operation_mechanism=self.spec.operation_mechanism,
            intent_keywords=sorted(keywords),
            tools=summaries,
        )

    @staticmethod
    def _build_schema(opt_lines: list[str]) -> dict:
        """把 --help 的 Options 段转成 JSON Schema（粗粒度，够 LLM 用）。"""
        props: dict[str, Any] = {}
        required: list[str] = []
        for line in opt_lines:
            p = _parse_option(line)
            if not p:
                continue
            flag, raw_type, _help = p
            json_type = _TYPE_MAP.get(raw_type, "string")
            arg_name = flag.lstrip("-").replace("-", "_")
            props[arg_name] = {"type": json_type, "description": _help}
        return {"type": "object", "properties": props, "required": required}

    # -- 调用 ---------------------------------------------------------------
    def invoke(self, tool: CLITool, args: dict, ctx: Optional[dict] = None) -> dict:
        """subprocess 调 CLI（带 --json），解析结构化返回。

        L2b 闸门：执行前 TrustGate.check()，默认 deny-unless-declared。
        cli_native 默认 ALLOW（不阻断 273 工具）；sdk_bridge 默认 ASK_USER。
        非 ALLOW 直接返回结构化结果，不执行。
        """
        spec = TrustGate.default_for_mechanism(self.spec.operation_mechanism)
        gate = TrustGate.check(spec, ctx)
        if gate.decision != ALLOW:
            return {"gate": gate.decision, "reason": gate.reason}

        cmd = [self.spec.cli_bin, "--json", *tool.subcommand]
        for k, v in args.items():
            cmd += [f"--{k.replace('_', '-')}", str(v)]
        env = dict(os.environ)
        # L2 两层发现：注入后端路径环境变量兜底 CLI-Anything macOS 路径 bug
        backend = self.resolve_backend()
        if backend and backend.env_value:
            env[backend.env_var] = backend.env_value
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False, env=env)
        out = proc.stdout.strip()
        try:
            return json.loads(out) if out else {"ok": proc.returncode == 0}
        except json.JSONDecodeError:
            return {"raw": out, "returncode": proc.returncode}

    # -- 注册进 Vermes ------------------------------------------------------
    def register(self, tools: list[CLITool]) -> int:
        """把内省出的工具注册进 tools/registry（懒导入，避免污染仓库导入）。

        顺带 build CapabilityIndex 写入 CAPABILITY_REGISTRY，供 L2a 阶段一粗筛。
        """
        try:
            from tools.registry import registry
        except Exception as exc:  # pragma: no cover - 仅 spike 环境差异
            raise RuntimeError(f"无法导入 tools.registry: {exc}")

        count = 0
        for t in tools:
            handler = self._make_handler(t)
            registry.register(
                name=t.name,
                toolset=t.toolset,
                schema=t.json_schema,
                handler=handler,
                description=t.description,
            )
            count += 1
        if tools:
            CAPABILITY_REGISTRY.add(self.build_capability_index(tools))
        return count

    def _make_handler(self, tool: CLITool) -> Callable:
        """返回一个符合注册表契约 ``handler(args, **kw) -> str`` 的闭包。

        与 tools/registry 的 handler 约定对齐：
        - 接收位置参数 ``args``（工具入参 dict）+ 透传 ``**kw``。
        - ``kw`` 中的 ``ctx``（由 model_tools.handle_function_call 注入）透传给
          adapter.invoke()，使 L2b 信任闸门能消费会话上下文（session_id 等）。
        - 返回 JSON 字符串（handler 契约），而非裸 dict。
        """
        adapter = self

        def _handler(args: dict, **kwargs) -> str:
            ctx = kwargs.get("ctx")
            result = adapter.invoke(tool, args or {}, ctx=ctx)
            return json.dumps(result, ensure_ascii=False)

        return _handler


def main() -> None:  # pragma: no cover - CLI 入口
    """spike 验证入口：python -m vermes_cli.adapters.software_adapter --cli <bin>"""
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--cli", default="cli-anything-freecad")
    ap.add_argument("--domain", default="3d")
    ap.add_argument("--software", default="freecad")
    ap.add_argument("--register", action="store_true", help="注册进 tools/registry")
    args = ap.parse_args()

    spec = SoftwareAdapterSpec(
        domain=args.domain, software=args.software, cli_bin=args.cli
    )
    adapter = SoftwareAdapter(spec)
    tools = adapter.discover_tools()
    print(f"[discover] {args.cli}: {len(tools)} 工具内省完成")
    for t in tools[:5]:
        print(f"  - {t.name}: {' '.join(t.subcommand)} | {t.description[:50]}")
    if args.register:
        n = adapter.register(tools)
        print(f"[register] 已注册 {n} 个工具到 toolset={adapter.toolset}")


if __name__ == "__main__":
    main()
