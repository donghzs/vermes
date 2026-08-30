"""P3-4 D7 domains/*.yaml 极简加载器 + 接线回归测试。

验证「加行业不改码」：cap→维度要求与 brick→domain 均来自 yaml 单一真相源，
不碰代码即可新增领域；yaml 缺失/损坏时 fail-open（model_capable 回退硬编码兜底）。
"""
import unittest

from vermes_cli.capabilities.domains import (
    domain_for_brick,
    load_domain_cap_dims,
    load_domains,
)


class TestDomainsLoader(unittest.TestCase):
    def test_load_domains_includes_3d(self):
        domains = load_domains(reload=True)
        self.assertTrue(any(d.get("domain") == "3d" for d in domains))

    def test_cap_dims_loaded_from_yaml(self):
        dims = load_domain_cap_dims(reload=True)
        # 首例 3d 领域的 cadir_* 工具要求 tools 维度（来自 domains/3d.yaml）
        self.assertEqual(dims.get("cadir_build"), {"tools"})
        self.assertEqual(dims.get("cadir_verify_stl"), {"tools"})
        # yaml 未声明的工具不在映射里
        self.assertNotIn("no_such_cap_xyz", dims)

    def test_domain_for_brick_tags_3d_members(self):
        self.assertEqual(domain_for_brick("cadir"), "3d")
        self.assertEqual(domain_for_brick("mfgcad"), "3d")
        # 非 3d 领域 brick 不被打标
        self.assertIsNone(domain_for_brick("scholarforge"))


class TestModelCapableUsesLoader(unittest.TestCase):
    def test_model_capable_reads_domains_yaml(self):
        from vermes_cli.capabilities.module_service import model_capable

        # cadir_build 在 domains/3d.yaml 声明要求 tools 维度
        chk = model_capable("cadir_build", provider="__nonexistent_provider__")
        self.assertEqual(chk["required"], ["tools"])
        self.assertTrue(chk["ok"])  # 未知 provider fail-open

        # yaml/兜底均未声明的工具 → 无维度要求
        chk2 = model_capable("no_such_cap_xyz", provider="__nonexistent_provider__")
        self.assertEqual(chk2["required"], [])


class TestBrickDomainTagging(unittest.TestCase):
    def test_module_brick_tagged_with_domain_from_yaml(self):
        from vermes_cli.capabilities.registry import get_brick_registry

        bricks = get_brick_registry().discover(refresh=True)
        cadir = [b for b in bricks if b.id == "module:cadir"]
        self.assertTrue(cadir, "module:cadir 应被注册表发现")
        # domains/3d.yaml 把 cadir 打标为 3d（驱动 GET /api/v1/bricks?domain=3d）
        self.assertEqual(cadir[0].domain, "3d")


if __name__ == "__main__":
    unittest.main()
