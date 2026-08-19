from capo_s3 import Credentials, ProfileCredentialsProvider
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


def test_session_profile_becomes_a_credentials_provider():
    provider = S3Storage(bucket="b", session_profile="dev")._credentials_provider()  # noqa: SLF001
    assert isinstance(provider, ProfileCredentialsProvider)


def test_explicit_credentials_skip_the_profile_provider():
    creds = Credentials(access_key="a", secret_key="b")  # noqa: S106
    storage = S3Storage(bucket="b", session_profile="dev", credentials=creds)
    assert storage._credentials_provider() is None  # noqa: SLF001


def test_handler_wrapped_in_proxy_when_proxies_set():
    handler = S3Storage(bucket="b", proxies={"https": "http://proxy:8080"})._http_handler()  # noqa: SLF001
    assert isinstance(handler, ProxyHandler)


def test_public_api_is_importable_from_the_package_root():
    for name in django_capo_s3.__all__:
        assert hasattr(django_capo_s3, name)
