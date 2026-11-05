import ipaddress

import pytest
import yaml
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware


COMPOSE_PATH = __file__.replace("tests/test_deploy_config.py", "docker-compose.yml")


@pytest.fixture(scope="module")
def compose() -> dict:
    with open(COMPOSE_PATH, "r", encoding="utf-8") as compose_file:
        return yaml.safe_load(compose_file)


def test_loopback_ports_and_no_database_port(compose: dict) -> None:
    services = compose["services"]

    assert services["app"]["ports"] == ["127.0.0.1:8000:8000"]
    assert services["dashboard"]["ports"] == ["127.0.0.1:8501:8501"]
    assert "ports" not in services["db"]


def test_app_forwards_only_exact_gateway_ip(compose: dict) -> None:
    environment = compose["services"]["app"]["environment"]

    assert isinstance(environment, dict)
    assert set(environment) == {"FORWARDED_ALLOW_IPS"}

    value = environment["FORWARDED_ALLOW_IPS"]
    assert isinstance(value, str)
    assert value == str(ipaddress.IPv4Address(value))
    assert value != "*"
    assert "/" not in value


async def _run_proxy_middleware(
    peer: str,
    forwarded_for: str,
) -> tuple[str, int]:
    captured: dict[str, tuple[str, int]] = {}

    async def app(scope: dict, receive: object, send: object) -> None:
        captured["client"] = scope["client"]

    middleware = ProxyHeadersMiddleware(
        app,
        trusted_hosts="172.18.0.1",
    )
    scope = {
        "type": "http",
        "client": (peer, 12345),
        "headers": [(b"x-forwarded-for", forwarded_for.encode("latin1"))],
        "scheme": "http",
    }

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message: dict) -> None:
        return None

    await middleware(scope, receive, send)
    return captured["client"]


@pytest.mark.asyncio
async def test_uvicorn_uses_forwarded_client_from_trusted_gateway() -> None:
    client = await _run_proxy_middleware(
        peer="172.18.0.1",
        forwarded_for="203.0.113.10",
    )

    assert client == ("203.0.113.10", 0)


@pytest.mark.asyncio
async def test_uvicorn_uses_first_untrusted_ip_in_forwarded_chain() -> None:
    client = await _run_proxy_middleware(
        peer="172.18.0.1",
        forwarded_for="203.0.113.10, 198.51.100.20",
    )

    assert client == ("198.51.100.20", 0)


@pytest.mark.asyncio
async def test_uvicorn_ignores_forwarded_header_from_untrusted_peer() -> None:
    client = await _run_proxy_middleware(
        peer="192.0.2.25",
        forwarded_for="203.0.113.10",
    )

    assert client == ("192.0.2.25", 12345)
