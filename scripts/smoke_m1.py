"""M1 冒烟脚本：对「已启动的服务」跑一遍鉴权主链路。

与 pytest 的区别：pytest 用内存库 + 依赖覆盖，验证的是逻辑；
本脚本打真实 HTTP 端口、落真实 SQLite 文件，验证的是「部署形态能不能用」。
刻意不打印 token 与密码，只输出 PASS / FAIL。

用法：先启动服务，再执行
    .venv/Scripts/python.exe scripts/smoke_m1.py [base_url]
"""

import sys
import uuid

import httpx

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
API = f"{BASE_URL}/api/v1"

USERNAME = f"smoke_{uuid.uuid4().hex[:8]}"
EMAIL = f"{USERNAME}@example.com"
PASSWORD = "s3cret-pass"

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str) -> None:
    results.append((ok, label))
    print(f"{'PASS' if ok else 'FAIL'}  {label}")


def main() -> int:
    with httpx.Client(timeout=10.0) as client:
        resp = client.get(f"{API}/health/ready")
        data = resp.json().get("data", {})
        check(resp.status_code == 200, "就绪检查返回 200")
        check(data.get("components", {}).get("database", {}).get("ready") is True, "database 组件 ready=true")

        resp = client.post(
            f"{API}/auth/register",
            json={"username": USERNAME, "email": EMAIL, "password": PASSWORD},
        )
        check(resp.status_code == 201, "注册返回 201")
        check("hashed_password" not in resp.text, "注册响应不含 hashed_password")

        resp = client.post(f"{API}/auth/login", json={"username": USERNAME, "password": PASSWORD})
        check(resp.status_code == 200, "登录返回 200")
        token = resp.json().get("data", {}).get("access_token", "")
        check(bool(token), "登录返回 access_token")

        resp = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}"})
        check(resp.status_code == 200 and resp.json()["data"]["username"] == USERNAME, "取当前用户成功")

        resp = client.get(f"{API}/auth/me")
        check(resp.status_code == 401, "不带 token 返回 401")

        resp = client.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {token}tampered"})
        check(resp.status_code == 401, "篡改 token 返回 401")

        resp = client.post(f"{API}/auth/login", json={"username": USERNAME, "password": "wrong-pass"})
        check(resp.status_code == 401 and resp.json()["code"] == 2001, "错误密码返回 401 / 2001")

        resp = client.get(f"{API}/nope")
        check(resp.status_code == 404 and resp.json()["code"] == 3004, "未知路由返回 404 / 3004")

    failed = [label for ok, label in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} 通过")
    if failed:
        print("失败项：" + "; ".join(failed))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
