"""P3-4 D7：domains/*.yaml 极简加载器（「加行业不改码」单一真相源）。

每个 ``domains/<domain>.yaml`` 声明一个行业领域：

    domain: 3d                       # 领域标识
    bricks: [cadir, mfgcad]          # 属于该领域的 module brick（驱动 BrickRegistry.domain 打标）
    caps:                           # 工具级 → 所需模型能力维度（外置原 CAP_REQUIRED_DIMS，驱动 model_capable 灰显）
      cadir_build: [tools]
      cadir_compile: [tools]
      cadir_verify_step: [tools]
      cadir_verify_stl: [tools]

设计纪律（呼应抗拒过度设计）：
  · 不新建机制，纯消费 yaml；dom<->code 零耦合，新增领域只加一个 yaml。
  · 读取 fail-open：yaml 缺失/损坏不阻断，cap 维度回退到 module_service 硬编码兜底。
  · 聚合结果按 TTL 缓存，避免每次 discover / 每次 cap 查询都扫盘。
"""
from __future__ import annotations

import glob
import os
import time
from typing import Any, Dict, List, Optional, Set

_DOMAINS_DIR = os.path.join(os.path.dirname(__file__), "domains")
_CACHE_TTL = 60.0

_DOMAINS_CACHE: Optional[List[Dict[str, Any]]] = None
_DOMAINS_TS: float = 0.0


def load_domains(reload: bool = False) -> List[Dict[str, Any]]:
    """聚合所有 ``domains/*.yaml``，返回 ``[{domain, bricks, caps}, ...]``（缓存 TTL）。"""
    global _DOMAINS_CACHE, _DOMAINS_TS
    now = time.time()
    if not reload and _DOMAINS_CACHE is not None and (now - _DOMAINS_TS) < _CACHE_TTL:
        return _DOMAINS_CACHE

    result: List[Dict[str, Any]] = []
    if os.path.isdir(_DOMAINS_DIR):
        for path in sorted(glob.glob(os.path.join(_DOMAINS_DIR, "*.yaml"))):
            try:
                import yaml

                with open(path, "r", encoding="utf-8") as fh:
                    data = yaml.safe_load(fh) or {}
            except Exception:  # noqa: BLE001 - 坏文件不致命
                continue
            if isinstance(data, dict) and data.get("domain"):
                result.append(data)
    _DOMAINS_CACHE = result
    _DOMAINS_TS = now
    return result


def load_domain_cap_dims(reload: bool = False) -> Dict[str, Set[str]]:
    """聚合所有 domain 的 ``caps``（工具→所需能力维度），返回 ``{tool: {dims}}``。"""
    out: Dict[str, Set[str]] = {}
    for d in load_domains(reload=reload):
        for tool, dims in (d.get("caps") or {}).items():
            if isinstance(dims, (list, tuple, set)):
                out.setdefault(tool, set()).update(str(x) for x in dims)
    return out


def domain_for_brick(brick_name: str) -> Optional[str]:
    """返回某 brick 所属行业 domain（来自 domains/*.yaml 的 ``bricks`` 列表）。"""
    for d in load_domains():
        if brick_name in (d.get("bricks") or []):
            return d.get("domain")
    return None
