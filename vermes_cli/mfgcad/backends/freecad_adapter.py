"""FreeCADAdapter — ProToolAdapter 的首个参考实现（M1-2）。

架构分工（见 PRO_TOOL_ADAPTER_DESIGN.md §2/§4）：
- 本文件（FreeCADAdapter）：**纯传输 + 响应解析**。不 import FreeCAD，可在主 venv 直接
  import；FreeCAD 缺失时 `is_available()` 返回 False（优雅降级，不抛异常）。
- `vermes_freecad_bridge.py`：独立的 headless FreeCAD 子进程（由 freecadcmd 拉起），
  承载 §4.3 编辑操作 → FreeCAD 原语翻译表 与 §4.4 特征树提取。
- 上层（web_server / agent 工具 / 前端）只消费 ProToolAdapter 契约，不感知后端。

测试性：所有 I/O 收敛到 `_transport(cmd, session_id, payload)`，测试可注入假 transport
验证「请求构造 + 响应解析」真实逻辑，无需 FreeCAD（FreeCAD 翻译逻辑由 M1-6 真机 PoC 覆盖）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Optional

from .base import AdapterResult, EditOp, FeatureNode, ProToolAdapter

_DEFAULT_ENGINE_CMD = Path.home() / ".vermes" / "engines" / "freecad" / "freecadcmd"
_DEFAULT_SESSIONS_ROOT = Path.home() / ".vermes" / "mfgcad" / "sessions"


def _to_feature_nodes(tree_json: Optional[list[dict]]) -> list[FeatureNode]:
    if not tree_json:
        return []
    return [
        FeatureNode(
            id=str(n["id"]),
            kind=str(n.get("kind", "feature")),
            label=str(n.get("label", n["id"])),
            params=dict(n.get("params", {}) or {}),
        )
        for n in tree_json
    ]


class FreeCADAdapter(ProToolAdapter):
    name = "freecad"

    def __init__(
        self,
        sessions_root: Optional[str | Path] = None,
        freecadcmd: Optional[str | Path] = None,
    ) -> None:
        self.sessions_root = Path(sessions_root or _DEFAULT_SESSIONS_ROOT)
        self._freecadcmd_override = Path(freecadcmd) if freecadcmd else None
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        # 测试注入点：替换 _transport 即可脱离 FreeCAD 验证解析逻辑
        self._transport = self._real_transport  # type: ignore[assignment]

    # ── 引擎可用性 ────────────────────────────────────────

    def _locate_freecadcmd(self) -> Optional[Path]:
        if self._freecadcmd_override is not None:
            return self._freecadcmd_override if self._freecadcmd_override.exists() else None
        if _DEFAULT_ENGINE_CMD.exists():
            return _DEFAULT_ENGINE_CMD
        # macOS 常见安装位置
        for c in [
            "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd",
            "/opt/homebrew/opt/freecad/libexec/bin/freecadcmd",
        ]:
            if Path(c).exists():
                return Path(c)
        # Linux 常见安装位置
        for c in [
            "/usr/bin/freecadcmd",
            "/usr/local/bin/freecadcmd",
        ]:
            if Path(c).exists():
                return Path(c)
        # Windows 常见安装位置
        for c in [
            r"C:\Program Files\FreeCAD 1.0\bin\freecadcmd.exe",
            r"C:\Program Files\FreeCAD\bin\freecadcmd.exe",
            r"C:\Program Files (x86)\FreeCAD 1.0\bin\freecadcmd.exe",
            r"C:\Program Files (x86)\FreeCAD\bin\freecadcmd.exe",
        ]:
            if Path(c).exists():
                return Path(c)
        # PATH 查找
        on_path = shutil.which("freecadcmd")
        if on_path:
            return Path(on_path)
        return None

    def is_available(self) -> bool:
        try:
            return self._locate_freecadcmd() is not None
        except Exception:
            return False

    def ensure_ready(self, auto_setup: bool = False) -> bool:
        """FreeCAD 就绪返回 True；auto_setup=True 时委托 engine_setup 下载（M1-4 落地）。"""
        if self.is_available():
            return True
        if auto_setup:
            try:
                from ..engine_setup import ensure_freecad_ready

                ok, _msg = ensure_freecad_ready(auto_setup=True)
                return bool(ok)
            except Exception:
                return False
        return False

    # ── 桥进程管理 ────────────────────────────────────────

    def _bridge_script(self) -> Path:
        # bridge 脚本在 mfgcad/ 下（与 backends/ 同级），不在 backends/ 内
        return Path(__file__).resolve().parent.parent / "vermes_freecad_bridge.py"

    def _start_bridge(self) -> None:
        cmd = self._locate_freecadcmd()
        if cmd is None:
            raise RuntimeError("freecadcmd 不可用：请安装 FreeCAD 引擎或走 build123d 兜底")
        self.sessions_root.mkdir(parents=True, exist_ok=True)
        # FreeCAD 1.1 的 freecadcmd 把脚本路径当文件参数导入而非执行；
        # 用 -c "exec(open(...).read())" 方式跑 bridge 脚本。
        # PYTHONUTF8=1 解决 FreeCAD 内置 Python 默认 ascii 编码读不了中文注释的问题。
        bridge = self._bridge_script()
        env = os.environ.copy()
        env["VERMES_MFG_SESSIONS_DIR"] = str(self.sessions_root)
        env["PYTHONUTF8"] = "1"
        inline = f"exec(open('{bridge}', encoding='utf-8').read())"
        self._proc = subprocess.Popen(
            [str(cmd), "-c", inline],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        # 读启动就绪信号（容忍偶发 banner 行）
        self._read_json_line()

    def _read_json_line(self) -> dict:
        assert self._proc is not None and self._proc.stdout is not None
        for _ in range(50):  # 最多跳过 50 行噪声
            line = self._proc.stdout.readline()
            if not line:
                raise RuntimeError("bridge 进程已退出（stdout 关闭）")
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                return obj
        raise RuntimeError("bridge 未返回有效 JSON")

    def _real_transport(self, cmd: str, session_id: str, payload: dict) -> dict:
        with self._lock:
            if self._proc is None or self._proc.poll() is not None:
                self._start_bridge()
            assert self._proc is not None and self._proc.stdin is not None
            req = {"cmd": cmd, "session_id": session_id, "payload": payload}
            self._proc.stdin.write(json.dumps(req) + "\n")
            self._proc.stdin.flush()
            return self._read_json_line()

    def _close_bridge(self) -> None:
        if self._proc is not None:
            try:
                if self._proc.stdin:
                    self._proc.stdin.close()
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None

    # ── ProToolAdapter 契约实现 ───────────────────────────

    def create_doc(self, session_id: str) -> Path:
        resp = self._transport("create_doc", session_id, {})
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "create_doc 失败"))
        return Path(resp.get("native_doc") or (self.sessions_root / session_id / "native.FCStd"))

    def open(self, doc_path: str) -> bool:
        sid = Path(doc_path).parent.name
        resp = self._transport("open", sid, {"doc_path": doc_path})
        return bool(resp.get("ok"))

    def import_step(self, session_id: str, step_path: str) -> AdapterResult:
        resp = self._transport("import_step", session_id, {"step_path": str(step_path)})
        if not resp.get("ok"):
            return AdapterResult.err(resp.get("error", "import_step 失败"))
        return AdapterResult.ok_result(
            feature_tree=_to_feature_nodes(resp.get("feature_tree")),
            native_doc=Path(resp["native_doc"]) if resp.get("native_doc") else None,
        )

    def get_feature_tree(self, session_id: str) -> list[FeatureNode]:
        resp = self._transport("feature_tree", session_id, {})
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "get_feature_tree 失败"))
        return _to_feature_nodes(resp.get("feature_tree"))

    def apply_edit_op(self, session_id: str, op: EditOp) -> AdapterResult:
        payload: dict[str, Any] = {"op": op.to_dict()}
        if op.params.get("export"):
            payload["export"] = op.params["export"]
        resp = self._transport("edit_op", session_id, payload)
        if not resp.get("ok"):
            return AdapterResult.err(resp.get("error", "apply_edit_op 失败"))
        exports = {k: Path(v) for k, v in (resp.get("exports") or {}).items()}
        return AdapterResult.ok_result(
            feature_tree=_to_feature_nodes(resp.get("feature_tree")),
            native_doc=Path(resp["native_doc"]) if resp.get("native_doc") else None,
            exports=exports,
        )

    def export(self, session_id: str, formats: list[str]) -> dict[str, Path]:
        resp = self._transport("export", session_id, {"formats": list(formats)})
        if not resp.get("ok"):
            raise RuntimeError(resp.get("error", "export 失败"))
        return {k: Path(v) for k, v in (resp.get("exports") or {}).items()}

    def close(self, session_id: str) -> None:
        try:
            self._transport("close", session_id, {})
        finally:
            # 单桥多 session：close 仅关文档；进程在适配器析构时回收
            pass

    def __del__(self):
        try:
            self._close_bridge()
        except Exception:
            pass
