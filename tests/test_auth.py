"""M1 鉴权链路测试。

覆盖：注册、重名、重复邮箱、密码强度、登录、取当前用户、错误分支、
以及一条安全断言——密码必须是 bcrypt 哈希而非明文。
"""

from sqlalchemy import select

from app.models.user import User

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"
READY = "/api/v1/health/ready"

VALID = {"username": "alice", "email": "alice@example.com", "password": "s3cret-pass"}


async def _register(client, **overrides):
    return await client.post(REGISTER, json={**VALID, **overrides})


async def test_register_returns_public_user_without_password(client):
    resp = await _register(client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["username"] == "alice"
    assert "hashed_password" not in body["data"]
    assert body["trace_id"]


async def test_register_duplicate_username_returns_409(client):
    await _register(client)
    resp = await _register(client, email="other@example.com")
    assert resp.status_code == 409
    assert resp.json()["code"] == 3000


async def test_register_duplicate_email_returns_409(client):
    await _register(client)
    resp = await _register(client, username="bob")
    assert resp.status_code == 409
    assert resp.json()["code"] == 3000


async def test_register_short_password_returns_422_param_code(client):
    resp = await _register(client, password="short")
    assert resp.status_code == 422
    assert resp.json()["code"] == 1000


async def test_login_then_me(client):
    await _register(client)

    resp = await client.post(
        LOGIN, json={"username": VALID["username"], "password": VALID["password"]}
    )
    assert resp.status_code == 200
    token = resp.json()["data"]["access_token"]

    resp = await client.get(ME, headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["data"]["username"] == "alice"


async def test_login_wrong_password_returns_401(client):
    await _register(client)

    resp = await client.post(
        LOGIN, json={"username": VALID["username"], "password": "wrong-password"}
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == 2001


async def test_login_unknown_user_returns_same_error_as_wrong_password(client):
    """两种失败必须返回同样的信息，否则可被用来枚举账号。"""
    resp = await client.post(
        LOGIN, json={"username": "nobody", "password": "whatever123"}
    )
    assert resp.status_code == 401
    assert resp.json()["message"] == "用户名或密码错误"


async def test_me_without_token_returns_401(client):
    resp = await client.get(ME)
    assert resp.status_code == 401
    assert resp.json()["code"] == 2001


async def test_me_with_tampered_token_returns_401(client):
    resp = await client.get(ME, headers={"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 401


async def test_password_is_stored_as_bcrypt_hash(client, session_factory):
    await _register(client)

    async with session_factory() as session:
        user = (await session.execute(select(User))).scalar_one()

    assert user.hashed_password != VALID["password"]
    assert user.hashed_password.startswith("$2")


async def test_health_ready_reports_database_ready(client):
    resp = await client.get(READY)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["ready"] is True
    assert data["components"]["database"]["ready"] is True
    assert data["components"]["llm"]["required"] is False


async def test_unknown_route_uses_unified_error_shape(client):
    resp = await client.get("/api/v1/nope")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == 3004
    assert set(body) == {"code", "message", "data", "trace_id"}
