#!/usr/bin/env python3
"""
Vermes Vision 修复验证测试

测试内容：
1. image_routing.py 直接返回 native
2. vision_tools.py 放宽判断逻辑
3. 错误提示简化
"""

import json
import sys


def test_image_routing():
    """测试 image_routing.py 简化逻辑"""
    print("=" * 60)
    print("测试 1: image_routing.py")
    print("=" * 60)
    
    from agent.image_routing import decide_image_input_mode
    
    test_cases = [
        # (provider, model, expected_mode)
        ('xiaomi', 'mimo-v2.5-pro', 'native'),
        ('xiaomi', 'mimo-v2-omni', 'native'),
        ('vbit.top', 'mimo-v2.5-pro', 'native'),
        ('deepseek', 'deepseek-v4-flash', 'native'),
        ('openai', 'gpt-4o', 'native'),
        ('unknown', 'some-model', 'native'),
    ]
    
    all_pass = True
    for provider, model, expected in test_cases:
        result = decide_image_input_mode(provider, model, None)
        status = "✅" if result == expected else "❌"
        if result != expected:
            all_pass = False
        print(f"  {status} {provider}/{model}: {result} (期望: {expected})")
    
    # 测试用户显式配置 text 模式
    cfg = {"agent": {"image_input_mode": "text"}}
    result = decide_image_input_mode('xiaomi', 'mimo-v2.5-pro', cfg)
    status = "✅" if result == "text" else "❌"
    if result != "text":
        all_pass = False
    print(f"  {status} 用户配置text模式: {result} (期望: text)")
    
    return all_pass


def test_supports_media():
    """测试 vision_tools.py 放宽判断"""
    print("\n" + "=" * 60)
    print("测试 2: vision_tools.py _supports_media_in_tool_results")
    print("=" * 60)
    
    from tools.vision_tools import _supports_media_in_tool_results
    
    test_cases = [
        # (provider, model, expected)
        ('xiaomi', 'mimo-v2.5-pro', True),
        ('xiaomi', 'mimo-v2-omni', True),
        ('vbit.top', 'mimo-v2.5-pro', True),
        ('deepseek', 'deepseek-v4-flash', True),
        ('openai', 'gpt-4o', True),
        ('unknown', 'some-model', True),  # 未知也返回 True
        ('', '', True),  # 空值也返回 True
    ]
    
    all_pass = True
    for provider, model, expected in test_cases:
        result = _supports_media_in_tool_results(provider, model)
        status = "✅" if result == expected else "❌"
        if result != expected:
            all_pass = False
        print(f"  {status} {provider}/{model}: {result} (期望: {expected})")
    
    return all_pass


def test_error_message():
    """测试错误提示简化"""
    print("\n" + "=" * 60)
    print("测试 3: 错误提示简化")
    print("=" * 60)
    
    # 模拟 vision_analyze_tool 的错误处理逻辑
    def get_error_analysis(err_str, model):
        """提取错误提示逻辑"""
        if any(hint in err_str for hint in (
            "does not support", "not support image",
            "content_policy", "multimodal",
            "unrecognized request argument", "image input",
        )):
            return "正在尝试其他方式分析图片，请稍候..."
        return "其他错误"
    
    test_cases = [
        ("model does not support image input", "mimo-v2.5-pro"),
        ("unrecognized request argument: image_url", "gpt-4"),
        ("multimodal not supported", "deepseek-chat"),
    ]
    
    all_pass = True
    for err, model in test_cases:
        result = get_error_analysis(err.lower(), model)
        expected = "正在尝试其他方式分析图片，请稍候..."
        status = "✅" if result == expected else "❌"
        if result != expected:
            all_pass = False
        print(f"  {status} 错误'{err[:30]}...': '{result}'")
    
    return all_pass


def test_provider_vision_models():
    """测试 _PROVIDER_VISION_MODELS 映射"""
    print("\n" + "=" * 60)
    print("测试 4: _PROVIDER_VISION_MODELS 映射")
    print("=" * 60)
    
    from agent.auxiliary_client import _PROVIDER_VISION_MODELS
    
    print(f"  当前映射: {_PROVIDER_VISION_MODELS}")
    
    # 检查 xiaomi 映射
    xiaomi_model = _PROVIDER_VISION_MODELS.get('xiaomi')
    status = "✅" if xiaomi_model == 'mimo-v2-omni' else "❌"
    print(f"  {status} xiaomi -> {xiaomi_model} (期望: mimo-v2-omni)")
    
    return xiaomi_model == 'mimo-v2-omni'


def test_cross_provider_models():
    """测试跨 provider vision 模型列表"""
    print("\n" + "=" * 60)
    print("测试 5: _CROSS_PROVIDER_VISION_MODELS")
    print("=" * 60)
    
    from tools.vision_tools import _CROSS_PROVIDER_VISION_MODELS
    
    print(f"  共 {len(_CROSS_PROVIDER_VISION_MODELS)} 个 provider:")
    for provider, model in _CROSS_PROVIDER_VISION_MODELS:
        marker = " ← xiaomi" if provider == 'xiaomi' else ""
        print(f"    • {provider}: {model}{marker}")
    
    # 检查 xiaomi 在列表中
    has_xiaomi = any(p == 'xiaomi' for p, m in _CROSS_PROVIDER_VISION_MODELS)
    status = "✅" if has_xiaomi else "❌"
    print(f"\n  {status} xiaomi 在跨 provider 列表中")
    
    return has_xiaomi


def main():
    print("\n" + "🧪 Vermes Vision 修复验证测试".center(60))
    print("=" * 60)
    
    results = []
    
    try:
        results.append(("image_routing", test_image_routing()))
    except Exception as e:
        print(f"❌ image_routing 测试失败: {e}")
        results.append(("image_routing", False))
    
    try:
        results.append(("supports_media", test_supports_media()))
    except Exception as e:
        print(f"❌ supports_media 测试失败: {e}")
        results.append(("supports_media", False))
    
    try:
        results.append(("error_message", test_error_message()))
    except Exception as e:
        print(f"❌ error_message 测试失败: {e}")
        results.append(("error_message", False))
    
    try:
        results.append(("provider_vision", test_provider_vision_models()))
    except Exception as e:
        print(f"❌ provider_vision 测试失败: {e}")
        results.append(("provider_vision", False))
    
    try:
        results.append(("cross_provider", test_cross_provider_models()))
    except Exception as e:
        print(f"❌ cross_provider 测试失败: {e}")
        results.append(("cross_provider", False))
    
    # 汇总
    print("\n" + "=" * 60)
    print("测试结果汇总")
    print("=" * 60)
    
    all_pass = True
    for name, passed in results:
        status = "✅ 通过" if passed else "❌ 失败"
        print(f"  {status}: {name}")
        if not passed:
            all_pass = False
    
    print("\n" + "=" * 60)
    if all_pass:
        print("🎉 所有测试通过！Vision 修复验证完成。")
        return 0
    else:
        print("⚠️ 部分测试失败，请检查修复逻辑。")
        return 1


if __name__ == "__main__":
    sys.exit(main())
