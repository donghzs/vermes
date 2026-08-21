"""Vermes SoftwareAdapter 薄插槽包（L2 层）。

见 UNIVERSAL_OPERATION_LAYER_DESIGN.md §5。本包是 Vermes 唯一新增代码面：
只做「挂接 + 内省 + 注册」，绝不写垂直逻辑。垂直能力由 L3 操作层
（CLI-Anything 等社区轮子）提供。
"""
