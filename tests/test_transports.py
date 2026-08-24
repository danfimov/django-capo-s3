import logging
from collections.abc import Callable
from datetime import timedelta

import pytest
from capo_s3 import S3Client
from django.core.files.base import ContentFile
from pyreqwest.client import SyncClientBuilder
from zapros import PyreqwestHandler

from django_capo_s3 import core
from django_capo_s3.storage import S3Storage


def test_the_documented_pairing_builds_a_rust_backed_handler():
    storage = S3Storage(bucket="b", http_client_builder=SyncClientBuilder, http_handler=PyreqwestHandler)
    assert isinstance(storage.http_handler, PyreqwestHandler)


@pytest.mark.parametrize(
    "options",
    [
        pytest.param({"connect_timeout": 2.0}, id="connect-timeout"),
        pytest.param({"read_timeout": 30.0}, id="read-timeout"),
        pytest.param({"total_timeout": 60.0}, id="total-timeout-via-timeout"),
        pytest.param({"max_connections_per_host": 16}, id="pool-size"),
        pytest.param({"connect_timeout": 2.0, "read_timeout": 30.0, "max_connections_per_host": 8}, id="combined"),
    ],
)
def test_transport_options_are_accepted_by_the_real_builder(options: dict[str, object]):
    # reqwest rejects the wrong type outright — seconds where it wants a timedelta — so building a handler
    # without raising is what proves each option is converted the way pyreqwest expects.
    storage = S3Storage(bucket="b", http_client_builder=SyncClientBuilder, http_handler=PyreqwestHandler, **options)  # type: ignore[arg-type]
    assert isinstance(storage.http_handler, PyreqwestHandler)


@pytest.mark.parametrize(
    "options",
    [
        pytest.param({}, id="untouched"),
        pytest.param({"connect_timeout": 2.0, "read_timeout": 30.0, "total_timeout": 60.0}, id="every-timeout"),
        pytest.param({"max_connections_per_host": 8}, id="pool-size"),
        pytest.param({"proxies": {"https": "http://proxy.internal:8080"}}, id="proxies-left-to-the-builder"),
        pytest.param({"verify": "/etc/ssl/ca.pem"}, id="verify-left-to-the-builder"),
        pytest.param({"write_timeout": 5.0}, id="write-timeout-with-no-equivalent"),
    ],
)
def test_a_swept_builder_still_builds_a_client(options: dict[str, object]):
    # One level down from the storage: whatever the sweep does or declines to do, the builder pyreqwest ships
    # must come out of it able to produce a client.
    assert core.configure_builder(SyncClientBuilder(), options).build() is not None


@pytest.mark.parametrize(
    ("options", "reported"),
    [
        pytest.param({"write_timeout": 5.0}, "Ignoring write_timeout", id="no-such-builder-method"),
        pytest.param({"proxies": {"https": "http://p:8080"}}, "Ignoring proxies", id="proxies-need-a-proxybuilder"),
        pytest.param({"verify": False}, "Ignoring verify", id="verify-off"),
        pytest.param({"verify": "/etc/ssl/ca.pem"}, "Ignoring verify", id="verify-ca-bundle"),
        pytest.param({"verify": True}, None, id="verify-at-its-default-stays-quiet"),
        pytest.param({"read_timeout": 30.0}, None, id="a-mapped-option-stays-quiet"),
    ],
)
def test_options_the_builder_cannot_take_are_reported(
    options: dict[str, object],
    reported: str | None,
    caplog: pytest.LogCaptureFixture,
):
    # reqwest has no per-write timeout, and wants a ProxyBuilder and parsed certificates that the storage has
    # no way to construct — so these are reported rather than half-applied.
    with caplog.at_level(logging.WARNING, logger="django_capo_s3.core"):
        core.configure_builder(SyncClientBuilder(), options)
    if reported is None:
        assert caplog.text == ""
    else:
        assert reported in caplog.text


def test_reqwest_really_has_no_write_timeout():
    # Pins the premise of the warning above: it's reqwest's API, not an accident of the test setup.
    assert not hasattr(SyncClientBuilder(), "write_timeout")


def test_timeouts_must_be_timedelta_not_seconds():
    # Pins the reason configure_builder converts at all; if this ever stops raising, the conversion is moot.
    with pytest.raises(TypeError, match="timedelta"):
        SyncClientBuilder().connect_timeout(2.0)  # type: ignore[arg-type]
    assert SyncClientBuilder().connect_timeout(timedelta(seconds=2)) is not None


@pytest.mark.timeout(10)
def test_objects_round_trip_over_the_rust_transport(
    storage_factory: Callable[..., S3Storage],
    bucket: str,
    s3_client: S3Client,
):
    storage = storage_factory(
        http_client_builder=SyncClientBuilder, http_handler=PyreqwestHandler, connect_timeout=5.0, read_timeout=30.0
    )
    assert isinstance(storage.http_handler, PyreqwestHandler)  # else this passes on the default transport
    payload = b"routed through reqwest"
    storage.save("rust/report.txt", ContentFile(payload))

    with s3_client.get_object(bucket, "rust/report.txt") as out:  # a plain client sees the stored object
        assert b"".join(out["body"]) == payload
        assert out.get("content_type") == "text/plain"

    with storage.open("rust/report.txt") as handle:  # and reads come back over the same transport
        assert handle.read() == payload
