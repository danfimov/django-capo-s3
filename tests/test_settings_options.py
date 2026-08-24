import pytest
from django.test import override_settings

from django_capo_s3 import core
from django_capo_s3.static import S3StaticStorage
from django_capo_s3.storage import S3Storage


def test_no_aws_settings_means_nothing_to_report():
    assert core.options_from_settings() == {}


@pytest.mark.parametrize(
    ("setting", "value", "option", "expected"),
    [
        pytest.param("AWS_STORAGE_BUCKET_NAME", "from-settings", "bucket", "from-settings", id="bucket"),
        pytest.param("AWS_LOCATION", "media", "location", "media", id="location"),
        pytest.param("AWS_S3_ENDPOINT_URL", "http://minio:9000", "endpoint", "http://minio:9000", id="endpoint"),
        pytest.param("AWS_S3_REGION_NAME", "eu-central-1", "region", "eu-central-1", id="region"),
        pytest.param("AWS_QUERYSTRING_AUTH", False, "querystring_auth", False, id="querystring-auth"),
        pytest.param("AWS_QUERYSTRING_EXPIRE", 60, "url_expire", 60, id="expiry-is-renamed"),
        pytest.param("AWS_S3_CUSTOM_DOMAIN", "cdn.example.com", "custom_domain", "cdn.example.com", id="domain"),
        pytest.param("AWS_CLOUDFRONT_KEY_ID", "K1ABCDEF", "cloudfront_key_id", "K1ABCDEF", id="cloudfront-key-id"),
        pytest.param("AWS_S3_FILE_OVERWRITE", False, "file_overwrite", False, id="file-overwrite"),
        pytest.param("AWS_DEFAULT_ACL", "private", "default_acl", "private", id="default-acl"),
        pytest.param("AWS_IS_GZIPPED", True, "gzip", True, id="gzip-is-renamed"),
        pytest.param("GZIP_CONTENT_TYPES", ("text/css",), "gzip_content_types", ("text/css",), id="gzip-types"),
        pytest.param("AWS_S3_MAX_MEMORY_SIZE", 0, "max_memory_size", 0, id="max-memory-size"),
        pytest.param("AWS_S3_SESSION_PROFILE", "dev", "session_profile", "dev", id="session-profile"),
        pytest.param(
            "AWS_S3_OBJECT_PARAMETERS",
            {"cache_control": "max-age=60"},
            "object_parameters",
            {"cache_control": "max-age=60"},
            id="object-parameters",
        ),
    ],
)
def test_a_setting_fills_in_its_option(setting: str, value: object, option: str, expected: object):
    with override_settings(**{setting: value}):
        assert core.options_from_settings()[option] == expected  # type: ignore[literal-required]


@pytest.mark.parametrize(
    ("protocol", "expected"),
    [
        pytest.param("https:", "https", id="trailing-colon-is-dropped"),
        pytest.param("http:", "http", id="plain-http"),
        pytest.param("https", "https", id="already-bare"),
    ],
)
def test_url_protocol_loses_the_trailing_colon(protocol: str, expected: str):
    # django-storages writes this setting with a colon; the option is the bare scheme.
    with override_settings(AWS_S3_URL_PROTOCOL=protocol):
        assert core.options_from_settings()["url_protocol"] == expected


@pytest.mark.parametrize(
    ("style", "expected"),
    [
        pytest.param("path", True, id="path-style"),
        pytest.param("virtual", False, id="virtual-host-style"),
    ],
)
def test_addressing_style_becomes_a_boolean(style: str, *, expected: bool):
    with override_settings(AWS_S3_ADDRESSING_STYLE=style):
        assert core.options_from_settings()["force_path_style"] is expected


@pytest.mark.parametrize(
    ("verify", "reported"),
    [
        pytest.param(False, True, id="explicitly-off"),
        pytest.param("/etc/ssl/ca.pem", True, id="ca-bundle"),
        pytest.param(None, False, id="none-means-library-default"),
    ],
)
def test_verify_is_only_taken_when_it_says_something(verify: object, *, reported: bool):
    with override_settings(AWS_S3_VERIFY=verify):
        assert ("verify" in core.options_from_settings()) is reported


@pytest.mark.parametrize(
    ("settings_kwargs", "expected"),
    [
        pytest.param(
            {"AWS_S3_ACCESS_KEY_ID": "AKIA", "AWS_S3_SECRET_ACCESS_KEY": "shh"},
            {"access_key": "AKIA", "secret_key": "shh"},
            id="s3-prefixed-keys",
        ),
        pytest.param(
            {"AWS_ACCESS_KEY_ID": "AKIA", "AWS_SECRET_ACCESS_KEY": "shh"},
            {"access_key": "AKIA", "secret_key": "shh"},
            id="unprefixed-keys",
        ),
        pytest.param(
            {"AWS_ACCESS_KEY_ID": "AKIA", "AWS_SECRET_ACCESS_KEY": "shh", "AWS_SESSION_TOKEN": "tok"},
            {"access_key": "AKIA", "secret_key": "shh", "session_token": "tok"},
            id="with-a-session-token",
        ),
        pytest.param({"AWS_ACCESS_KEY_ID": "AKIA"}, None, id="half-a-pair-is-ignored"),
        pytest.param({"AWS_SECRET_ACCESS_KEY": "shh"}, None, id="secret-without-a-key"),
    ],
)
def test_credentials_are_assembled_from_the_key_settings(settings_kwargs: dict[str, str], expected: dict | None):
    # Leaving them out matters: capo then resolves from the environment, IRSA or SSO instead.
    with override_settings(**settings_kwargs):
        assert core.options_from_settings().get("credentials") == expected


@override_settings(AWS_STORAGE_BUCKET_NAME="from-settings", AWS_S3_REGION_NAME="eu-central-1")
def test_a_storage_can_be_configured_entirely_from_settings():
    storage = S3Storage()
    assert storage.bucket == "from-settings"
    assert storage.options["region"] == "eu-central-1"


@override_settings(AWS_STORAGE_BUCKET_NAME="from-settings", AWS_QUERYSTRING_EXPIRE=60)
def test_options_win_over_settings():
    storage = S3Storage(bucket="from-options", url_expire=120)
    assert storage.bucket == "from-options"
    assert storage.options["url_expire"] == 120


@override_settings(AWS_QUERYSTRING_EXPIRE=60)
def test_settings_win_over_the_defaults():
    assert S3Storage(bucket="b").options["url_expire"] == 60
    assert core.DEFAULTS["url_expire"] == 3600


@override_settings(AWS_S3_FILE_OVERWRITE=False, AWS_QUERYSTRING_AUTH=True)
def test_static_storage_keeps_the_defaults_it_depends_on():
    # Hashed names are overwritten in place and served unsigned, so these two aren't settings' to take back.
    storage = S3StaticStorage(bucket="b")
    assert storage.options["file_overwrite"] is True
    assert storage.options["querystring_auth"] is False
