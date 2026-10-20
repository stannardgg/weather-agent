"""【M6-Q2】审计日志埋点测试。

验证关键操作确实落审计：注册 → register、登录 → login。
通过 client 发真实请求，再用 `audit_crud.list_recent` 查库断言（用同一个
in-memory 数据库，StaticPool 保证同一连接，查得到请求里写入的数据）。
"""

from app.crud import audit as audit_crud


async def test_register_records_audit(client, session_factory):
    response = await client.post(
        "/api/v1/auth/register",
        json={"username": "alice", "email": "alice@test.com", "password": "secret123"},
    )
    assert response.status_code == 201

    async with session_factory() as db:
        logs = await audit_crud.list_recent(db, action="register")
    assert len(logs) == 1
    assert logs[0].user_id is not None
    assert logs[0].target_type == "user"
    assert logs[0].target_id == logs[0].user_id


async def test_login_records_audit(client, session_factory):
    await client.post(
        "/api/v1/auth/register",
        json={"username": "bob", "email": "bob@test.com", "password": "secret123"},
    )
    response = await client.post(
        "/api/v1/auth/login",
        json={"username": "bob", "password": "secret123"},
    )
    assert response.status_code == 200

    async with session_factory() as db:
        logs = await audit_crud.list_recent(db, action="login")
    assert len(logs) == 1
    assert logs[0].user_id is not None


async def test_audit_log_captures_trace_id(client, session_factory):
    response = await client.post(
        "/api/v1/auth/register",
        json={"username": "carol", "email": "carol@test.com", "password": "secret123"},
        headers={"X-Trace-Id": "trace-abc-123"},
    )
    assert response.status_code == 201

    async with session_factory() as db:
        logs = await audit_crud.list_recent(db, action="register")
    # trace_id 由中间件绑定、审计自动读取，能把审计与访问日志串起来
    assert logs[0].trace_id == "trace-abc-123"
