from collections.abc import Mapping

from zapros import BaseHandler, Request, Response


class ProxyHandler(BaseHandler):
    """Wrap another handler and route each request through a scheme-matched proxy.

    proxies maps a URL scheme ("http" or "https") to a proxy URL; credentials can be embedded in the proxy
    URL as http://user:pass@host:port. Subclass and override select_proxy to change how a proxy is chosen.
    """

    def __init__(self, inner: BaseHandler, proxies: Mapping[str, str]) -> None:
        """Wrap inner and keep the scheme-to-proxy mapping used for each request."""
        self._inner = inner
        self._proxies = dict(proxies)

    def select_proxy(self, scheme: str) -> str | None:
        """Return the proxy URL for a request scheme ("http"/"https"), or None to go direct."""
        return self._proxies.get(scheme)

    def handle(self, request: Request) -> Response:
        """Attach the matching proxy to the request context, then delegate to the wrapped handler."""
        proxy = self.select_proxy(request.url.protocol.rstrip(":"))
        if proxy:
            request.context.setdefault("network", {})["proxy"] = {"url": proxy}
        return self._inner.handle(request)

    def close(self) -> None:
        """Close the wrapped handler and its connection pool."""
        self._inner.close()
