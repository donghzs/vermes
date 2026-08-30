"""P3-3 回归：4 个新端点必须穿透 auth_middleware（曾因未入 _PUBLIC_API_PATHS 被 401 拦截）。

锁死 P0 根因：端点未注册到 _PUBLIC_API_PATHS → auth_middleware 返回 401 →
前端裸 fetch / EventSource 全失效 → 「能力徽标灰显 / 徽标点击 invoke /
模型切换跨 tab 广播」三件事在桌面端全部不可用。

双层锁：
1. 数据层：4 端点必须在 _PUBLIC_API_PATHS（auth_middleware 读取的唯一真相源）。
2. 集成层：经真实 app（含完整 middleware 栈）发起无 token 请求，必须非 401。
   此前 P3-3 测试只直测 module_service 函数、不穿 middleware，故漏掉该跨层 bug。

注意：SSE 端点 /api/model-change/stream 不在集成层做真实请求——其生成器
`while True: client_q.get()` 在 TestClient 断开时不会自动取消（与浏览器
EventSource 行为不同），会挂死测试。其鉴权旁路由数据层成员断言精确锁定
（auth_middleware 对它的判定就是 `"路径" in _PUBLIC_API_PATHS`）。
"""
from __future__ import annotations

from vermes_cli.web_server import _PUBLIC_API_PATHS, app
from starlette.testclient import TestClient

P3_INVOKE_ENDPOINTS = (
    "/api/invoke",
    "/api/invoke/capable",
    "/api/model-change",
    "/api/model-change/stream",
)


def test_p3_invoke_endpoints_registered_public():
    """数据层锁根因：4 端点必须在 public 列表，否则被 401 拦截。"""
    for ep in P3_INVOKE_ENDPOINTS:
        assert ep in _PUBLIC_API_PATHS, (
            f"{ep} 必须加入 _PUBLIC_API_PATHS，否则 auth_middleware 拦截 401"
        )


def test_p3_invoke_endpoints_bypass_auth_middleware():
    """集成层锁：无 token 请求经真实 auth_middleware 必须非 401（穿透到路由）。

    raise_server_exceptions=False：handler 自身异常（500）不影响本测试锁定的
    鉴权旁路语义——本测试只关心「请求是否被 auth_middleware 以 401 拦下」。

    SSE 端点 /api/model-change/stream 的旁路由 test_p3_invoke_endpoints_registered_public
    锁定，此处不发起真实流式请求（见模块 docstring 关于挂死的说明）。
    """
    client = TestClient(app, raise_server_exceptions=False)

    # GET 打 POST-only 端点：public → 405（路由存在但方法不符），private → 401
    for ep in ("/api/invoke", "/api/model-change"):
        r = client.get(ep)
        assert r.status_code != 401, f"{ep} 未穿透 auth_middleware（返回 {r.status_code}）"

    # GET /api/invoke/capable?cap=... public → 到达 handler（200/422），非 401
    r = client.get("/api/invoke/capable?cap=chat")
    assert r.status_code != 401, (
        f"/api/invoke/capable 未穿透 auth_middleware（{r.status_code}）"
    )
