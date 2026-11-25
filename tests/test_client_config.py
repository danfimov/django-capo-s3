import shlex
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from capo_s3 import (
    AssumeRoleCredentialsProvider,
    CachedProvider,
    ChainedProvider,
    Credentials,
    ProcessCredentialsProvider,
    ProfileCredentialsProvider,
    SsoCredentialsProvider,
    WebIdentityCredentialsProvider,
)
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
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
        ProcessCredentialsProvider,
    ]
    assert all(p._profile == "dev" for p in inner)  # noqa: SLF001  # the profile is threaded to every provider


def test_explicit_credentials_skip_the_profile_provider():
    creds = Credentials(access_key="a", secret_key="b")  # noqa: S106
    storage = S3Storage(bucket="b", session_profile="dev", credentials=creds)
    assert storage._credentials_provider() is None  # noqa: SLF001


@pytest.mark.parametrize(
    ("credentials_file", "config_file", "expected_access_key"),
    [
        pytest.param(
            "[dev]\naws_access_key_id = AKIASTATIC\naws_secret_access_key = secret123\n",
            "",
            "AKIASTATIC",
            id="static-keys",
        ),
        pytest.param(
            "",
            "[profile dev]\ncredential_process = {helper}\n",
            "AKIAPROCESS",
            id="credential-process",
        ),
    ],
)
def test_when_a_session_profile_is_resolved_then_the_provider_holding_its_keys_is_reached(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    credentials_file: str,
    config_file: str,
    expected_access_key: str,
) -> None:
    # The providers ahead of the one holding the keys must skip cleanly (IdentityNotFound), otherwise the chain
    # stops before reaching it.
    helper = tmp_path / "helper.py"
    helper.write_text(
        'import json\nprint(json.dumps({"Version": 1, "AccessKeyId": "AKIAPROCESS", "SecretAccessKey": "secret123"}))\n'
    )
    (tmp_path / "credentials").write_text(credentials_file)
    (tmp_path / "config").write_text(
        config_file.format(helper=f"{shlex.quote(sys.executable)} {shlex.quote(str(helper))}")
    )
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    for var in ("AWS_WEB_IDENTITY_TOKEN_FILE", "AWS_ROLE_ARN", "AWS_PROFILE"):
        monkeypatch.delenv(var, raising=False)

    provider = S3Storage(bucket="b", session_profile="dev")._credentials_provider()  # noqa: SLF001
    resolved = provider.resolve_identity()  # type: ignore[union-attr]
    assert resolved["access_key"] == expected_access_key
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


@pytest.mark.parametrize(
    ("options", "aws_settings", "expected_error"),
    [
        pytest.param(
            {"credentials": Credentials(access_key="k", secret_key="s")},  # noqa: S106
            {},
            "cannot be combined with credentials",
            id="explicit-credentials-are-rejected",
        ),
        pytest.param({"session_profile": "dev"}, {}, "cannot be combined", id="explicit-profile-is-rejected"),
        pytest.param(
            {},
            {"AWS_ACCESS_KEY_ID": "k", "AWS_SECRET_ACCESS_KEY": "s"},
            None,
            id="global-keys-for-other-storages-are-tolerated",
        ),
    ],
)
def test_when_an_anonymous_storage_is_also_given_credentials_then_only_its_own_options_make_it_fail(
    options: S3StorageOptions, aws_settings: dict[str, str], expected_error: str | None
):
    with override_settings(**aws_settings):
        if expected_error is None:
            assert S3Storage(bucket="b", anonymous=True, **options).options["anonymous"]
        else:
            with pytest.raises(ImproperlyConfigured, match=expected_error):
                S3Storage(bucket="b", anonymous=True, **options)


@pytest.mark.parametrize(
    ("retry_mode", "expected_error"),
    [
        pytest.param(None, None, id="unset-is-left-to-aws-retry-mode-and-the-profile"),
        pytest.param("standard", None, id="standard"),
        pytest.param("adaptive", None, id="adaptive"),
        pytest.param("adaptiv", "retry_mode must be 'standard' or 'adaptive'", id="typo-fails-at-startup"),
        pytest.param("legacy", "retry_mode must be 'standard' or 'adaptive'", id="legacy-is-not-offered"),
    ],
)
def test_when_a_retry_mode_is_configured_then_the_client_is_built_with_it_or_the_storage_refuses_it(
    retry_mode: str | None, expected_error: str | None
):
    if expected_error is None:
        storage = S3Storage(bucket="b", retry_mode=retry_mode)  # type: ignore[typeddict-item]
        assert storage.client._config["retry_mode"] == retry_mode  # noqa: SLF001
    else:
        with pytest.raises(ImproperlyConfigured, match=expected_error):
            S3Storage(bucket="b", retry_mode=retry_mode)  # type: ignore[typeddict-item]


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
