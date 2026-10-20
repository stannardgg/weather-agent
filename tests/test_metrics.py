"""【M6-Q3】指标计数器与 /metrics 端点单测。

覆盖：
1. counter 累加与渲染（Prometheus 文本格式）；
2. summary 的 _sum / _count 分量；
3. /metrics 端点返回 200 + text/plain + 非空指标。
"""

from app.core import metrics as metrics_module


def test_counter_increments_and_renders():
    metrics_module.reset()
    metrics_module.inc_counter(
        "http_requests_total",
        "HTTP 请求总数",
        labels={"method": "GET", "path": "/x", "status": "200"},
    )
    metrics_module.inc_counter(
        "http_requests_total",
        "HTTP 请求总数",
        labels={"method": "GET", "path": "/x", "status": "200"},
    )

    out = metrics_module.render()
    assert "# HELP http_requests_total HTTP 请求总数" in out
    assert "# TYPE http_requests_total counter" in out
    assert 'http_requests_total{method="GET",path="/x",status="200"} 2' in out


def test_counter_labels_are_sorted():
    metrics_module.reset()
    metrics_module.inc_counter(
        "m", "help", labels={"b": "2", "a": "1"}
    )
    # label 按 key 排序，保证同一组 label 输出稳定
    assert 'm{a="1",b="2"} 1' in metrics_module.render()


def test_summary_renders_sum_and_count():
    metrics_module.reset()
    metrics_module.observe_summary("http_request_duration_seconds", "耗时", 0.5)
    metrics_module.observe_summary("http_request_duration_seconds", "耗时", 0.3)

    out = metrics_module.render()
    assert "# TYPE http_request_duration_seconds summary" in out
    assert "http_request_duration_seconds_sum 0.8" in out
    assert "http_request_duration_seconds_count 2" in out


def test_reset_clears_registry_and_series():
    metrics_module.inc_counter("m", "help")
    assert "m 1" in metrics_module.render()
    metrics_module.reset()
    assert metrics_module.render() == ""


async def test_metrics_endpoint_returns_prometheus_text(client):
    # 先打一个请求预热：trace 中间件在**响应返回之后**才把 http_requests_total 埋进去，
    # 所以 metrics 端点自己这一跳要等它自己响应完才会被计数——直接断言会看到空表。
    # 预热一次后，health 那一跳的计数已经落表，再抓 /metrics 就能看到非空指标了。
    warm = await client.get("/api/v1/health")
    assert warm.status_code == 200

    response = await client.get("/api/v1/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "http_requests_total" in response.text
