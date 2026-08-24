from datetime import timedelta

import pytest
from capo_s3 import (
    AssumeRoleCredentialsProvider,
    CachedProvider,
    ChainedProvider,
    Credentials,
    ProfileCredentialsProvider,
    SsoCredentialsProvider,
    WebIdentityCredentialsProvider,
)
from django.core.exceptions import ImproperlyConfigured
from zapros import StdNetworkHandler

import django_capo_s3
from django_capo_s3.core import S3StorageOptions
from django_capo_s3.proxy import ProxyHandler
from django_capo_s3.storage import S3Storage


def test_no_custom_handler_by_default():
    assert S3Storage(bucket="b").http_handler is None


def test_handler_built_when_a_timeout_is_set():
    handler = S3Storage(bucket="b", read_timeout=5.0).http_handler
    assert isinstance(handler, StdNetworkHandler)


def test_handler_built_when_pool_size_is_set():
    assert S3Storage(bucket="b", max_connections_per_host=50).http_handler is not None


def test_session_profile_builds_a_refreshing_chain():
    # A named profile must resolve role_arn / web_identity / SSO — not just static keys — and refresh on
    # expiry, so the provider is a CachedProvider wrapping the profile-scoped chain, not a bare profile reader.
    provider = S3Storage(bucket="b", session_profile="dev")._credentials_provider()  # noqa: SLF001
    assert isinstance(provider, CachedProvider)
    chain = provider._inner  # noqa: SLF001
    assert isinstance(chain, ChainedProvider)
    inner = chain._providers  # noqa: SLF001
    assert [type(p) for p in inner] == [
        AssumeRoleCredentialsProvider,
        WebIdentityCredentialsProvider,
        SsoCredentialsProvider,
        ProfileCredentialsProvider,
    ]
    assert all(p._profile == "dev" for p in inner)  # noqa: SLF001  # the profile is threaded to every provider


def test_explicit_credentials_skip_the_profile_provider():
    creds = Credentials(access_key="a", secret_key="b")  # noqa: S106
    storage = S3Storage(bucket="b", session_profile="dev", credentials=creds)
    assert storage._credentials_provider() is None  # noqa: SLF001


def test_session_profile_chain_resolves_static_keys(tmp_path, monkeypatch):
    # End-to-end: the role_arn / web_identity / SSO providers must skip cleanly (IdentityNotFound) so a
    # plain static-key profile still resolves through the chain — proving the fallthrough is wired correctly.
    creds_file = tmp_path / "credentials"
    creds_file.write_text("[dev]\naws_access_key_id = AKIAEXAMPLE\naws_secret_access_key = secret123\n")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(creds_file))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))  # empty: no role_arn/sso/web_identity
    for var in ("AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_ROLE_ARN", "AWS_PROFILE"):
        monkeypatch.delenv(var, raising=False)

    provider = S3Storage(bucket="b", session_profile="dev")._credentials_provider()  # noqa: SLF001
    resolved = provider.resolve_identity()  # type: ignore[union-attr]
    assert resolved["access_key"] == "AKIAEXAMPLE"
    assert resolved["secret_key"] == "secret123"  # noqa: S105


def test_handler_wrapped_in_proxy_when_proxies_set():
    handler = S3Storage(bucket="b", proxies={"https": "http://proxy:8080"}).http_handler
    assert isinstance(handler, ProxyHandler)


class _RecordingBuilder:
    """Records what the storage applies to it; the real builder is opaque about that."""

    def __init__(self) -> None:
        self.timeouts: list[object] = []

    def read_timeout(self, value: object) -> "_RecordingBuilder":
        self.timeouts.append(value)
        return self


def test_http_handler_receives_the_configured_builder_lazily_and_once():
    seen: list[object] = []

    def build(builder: object) -> StdNetworkHandler:
        seen.append(builder)
        return StdNetworkHandler()

    storage = S3Storage(bucket="b", read_timeout=5.0, http_client_builder=_RecordingBuilder, http_handler=build)
    assert seen == []  # nothing built at construction, so no pool is created before a fork

    first = storage.http_handler
    second = storage.http_handler
    assert first is second  # one handler, so the object and credential clients share a pool
    assert len(seen) == 1
    assert seen[0].timeouts == [timedelta(seconds=5)]  # type: ignore[attr-defined]


def test_http_handler_gets_none_when_no_builder_is_configured():
    seen: list[object] = []

    def build(builder: object) -> StdNetworkHandler:
        seen.append(builder)
        return StdNetworkHandler()

    assert isinstance(S3Storage(bucket="b", http_handler=build).http_handler, StdNetworkHandler)
    assert seen == [None]


def test_http_handler_must_be_a_callable():
    with pytest.raises(ImproperlyConfigured, match="http_handler must be a callable"):
        S3Storage(bucket="b", http_handler=StdNetworkHandler())  # type: ignore[typeddict-item]


def test_client_builder_must_be_a_callable():
    with pytest.raises(ImproperlyConfigured, match="http_client_builder must be a callable"):
        S3Storage(bucket="b", http_handler=lambda _: StdNetworkHandler(), http_client_builder=_RecordingBuilder())  # type: ignore[typeddict-item]


def test_client_builder_without_a_handler_is_rejected():
    with pytest.raises(ImproperlyConfigured, match="does nothing without an http_handler"):
        S3Storage(bucket="b", http_client_builder=_RecordingBuilder)


def test_http_handler_survives_a_for_region_clone():
    def build(_: S3StorageOptions) -> StdNetworkHandler:
        return StdNetworkHandler()

    storage = S3Storage(bucket="b", region="eu-central-1", http_handler=build)
    assert storage.for_region("sa-east-1").options["http_handler"] is build


def test_public_api_is_importable_from_the_package_root():
    for name in django_capo_s3.__all__:
        assert hasattr(django_capo_s3, name)
