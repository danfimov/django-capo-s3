import ssl

import pytest

from django_capo_s3 import core


def test_normalize_key_joins_location():
    assert core.normalize_key("media", "a/b.txt") == "media/a/b.txt"


def test_normalize_key_strips_leading_slash_and_backslashes():
    assert core.normalize_key("", "/a\\b.txt") == "a/b.txt"


def test_normalize_key_without_location():
    assert core.normalize_key("", "a.txt") == "a.txt"


def test_normalize_key_collapses_redundant_segments():
    assert core.normalize_key("media/", "./sub//file.txt") == "media/sub/file.txt"


def test_normalize_key_rejects_traversal():
    with pytest.raises(ValueError, match="traversal"):
        core.normalize_key("media", "../etc/passwd")


def test_guess_content_type_known():
    assert core.guess_content_type("report.txt") == "text/plain"


def test_guess_content_type_unknown():
    assert core.guess_content_type("blob") is None


def test_build_public_url_custom_domain():
    options: core.S3StorageOptions = {"bucket": "b", "custom_domain": "cdn.example.com"}
    assert core.build_public_url(options, "k/x.txt") == "https://cdn.example.com/k/x.txt"


def test_build_public_url_path_style_endpoint():
    options: core.S3StorageOptions = {
        "bucket": "b",
        "endpoint": "http://localhost:9000",
        "force_path_style": True,
    }
    assert core.build_public_url(options, "k.txt") == "http://localhost:9000/b/k.txt"


def test_build_public_url_virtual_host_endpoint():
    options: core.S3StorageOptions = {
        "bucket": "b",
        "endpoint": "https://s3.example.com",
        "force_path_style": False,
    }
    assert core.build_public_url(options, "k.txt") == "https://b.s3.example.com/k.txt"


def test_build_public_url_default_aws_region():
    options: core.S3StorageOptions = {"bucket": "b", "region": "eu-central-1"}
    expected = "https://b.s3.eu-central-1.amazonaws.com/k.txt"
    assert core.build_public_url(options, "k.txt") == expected


def test_build_public_url_respects_url_protocol():
    options: core.S3StorageOptions = {"bucket": "b", "custom_domain": "cdn.example.com", "url_protocol": "http"}
    assert core.build_public_url(options, "k.txt") == "http://cdn.example.com/k.txt"


def test_ssl_context_for_default_is_none():
    assert core.ssl_context_for(verify=True) is None


def test_ssl_context_for_false_disables_verification():
    context = core.ssl_context_for(verify=False)
    assert context is not None
    assert context.check_hostname is False
    assert context.verify_mode == ssl.CERT_NONE


def test_ssl_context_for_takes_the_cafile_branch(tmp_path):
    # A path that doesn't exist proves ssl.create_default_context(cafile=...) is used for a string verify.
    with pytest.raises(FileNotFoundError):
        core.ssl_context_for(verify=str(tmp_path / "missing-ca.pem"))


def test_defaults_do_not_include_bucket():
    assert "bucket" not in core.DEFAULTS
    assert core.DEFAULTS["querystring_auth"] is True
    assert core.DEFAULTS["location"] == ""
