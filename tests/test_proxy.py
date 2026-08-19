"""Unit tests for ProxyHandler (no network)."""

from unittest.mock import Mock

from django_capo_s3.proxy import ProxyHandler


def _request(scheme: str) -> Mock:
    request = Mock()
    request.url.protocol = f"{scheme}:"
    request.context = {}
    return request


def test_injects_matching_proxy_and_delegates():
    inner = Mock()
    request = _request("https")
    handler = ProxyHandler(inner, {"https": "http://proxy:8080"})

    handler.handle(request)

    assert request.context["network"]["proxy"] == {"url": "http://proxy:8080"}
    inner.handle.assert_called_once_with(request)


def test_goes_direct_when_scheme_not_configured():
    inner = Mock()
    request = _request("http")  # only https is configured below
    handler = ProxyHandler(inner, {"https": "http://proxy:8080"})

    handler.handle(request)

    assert "network" not in request.context
    inner.handle.assert_called_once_with(request)


def test_close_delegates_to_inner():
    inner = Mock()
    ProxyHandler(inner, {}).close()
    inner.close.assert_called_once()
