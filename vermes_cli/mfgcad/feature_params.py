"""H9 根治：特征参数持久化辅助。

把编辑操作参数（如 fillet radius）以 JSON 存入 FreeCAD 对象的自定义属性 VermesMeta，
持久化到 .FCStd 文件。子进程重启后从 VermesMeta 恢复，不再依赖内存 _FEATURE_PARAMS 全局 dict。

独立模块，不依赖 FreeCAD 的 App 模块——_save/_load 只操作对象的标准属性接口
（addProperty / setattr / getattr），可在 freecadcmd 和主 venv 中通用。
"""

from __future__ import annotations

import json
import logging

_log = logging.getLogger(__name__)

# FreeCAD 自定义属性名
VERMES_META_PROP = "VermesMeta"


def save_vermes_meta(obj, meta: dict) -> None:
    """把编辑参数以 JSON 存入 FreeCAD 对象自定义属性，持久化到 .FCStd。

    Args:
        obj: FreeCAD DocumentObject（有 addProperty/setPropertyStatus/属性接口）
        meta: 参数字典，如 {"fillet": {"radius": 2.5}}
    """
    prop = VERMES_META_PROP
    if not hasattr(obj, prop):
        try:
            obj.addProperty("App::PropertyString", prop, "Vermes", "Vermes edit-op params (JSON)")
        except Exception:
            # 非 FreeCAD 环境（测试 mock）可能 addProperty 签名不同
            pass
    try:
        obj.setPropertyStatus(prop, "Transient")  # 不参与几何 recompute
    except Exception:
        pass
    setattr(obj, prop, json.dumps(meta, ensure_ascii=False))


def load_vermes_meta(obj) -> dict | None:
    """从 FreeCAD 对象自定义属性恢复编辑参数。

    Returns:
        参数字典，或 None（无属性 / JSON 损坏）
    """
    prop = VERMES_META_PROP
    if hasattr(obj, prop):
        raw = getattr(obj, prop, "")
        if raw:
            try:
                return json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                _log.warning("VermesMeta 属性 JSON 解析失败，忽略")
    return None
