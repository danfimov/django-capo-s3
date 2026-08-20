from capo_s3 import (
    AssumeRoleCredentialsProvider,
    CachedProvider,
    ChainedProvider,
    Credentials,
    ProfileCredentialsProvider,
    SsoCredentialsProvider,
    WebIdentityCredentialsProvider,
)
from zapros import StdNetworkHandler

import django_capo_s3
from django_capo_s3.proxy import ProxyHandler
from django_capo_s3.storage import S3Storage


def test_no_custom_handler_by_default():
    assert S3Storage(bucket="b")._http_handler() is None  # noqa: SLF001


def test_handler_built_when_a_timeout_is_set():
    handler = S3Storage(bucket="b", read_timeout=5.0)._http_handler()  # noqa: SLF001
    assert isinstance(handler, StdNetworkHandler)


def test_handler_built_when_pool_size_is_set():
    assert S3Storage(bucket="b", max_connections_per_host=50)._http_handler() is not None  # noqa: SLF001


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
    handler = S3Storage(bucket="b", proxies={"https": "http://proxy:8080"})._http_handler()  # noqa: SLF001
    assert isinstance(handler, ProxyHandler)


def test_public_api_is_importable_from_the_package_root():
    for name in django_capo_s3.__all__:
        assert hasattr(django_capo_s3, name)
