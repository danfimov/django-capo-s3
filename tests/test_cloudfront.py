import base64
import json
from urllib.parse import parse_qs, urlsplit

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from django_capo_s3.cloudfront import CloudFrontSigner
from django_capo_s3.storage import S3Storage

_CLOUDFRONT_B64_REVERSE = str.maketrans("-~_", "+/=")


def _make_key() -> tuple[rsa.RSAPrivateKey, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ).decode()
    return key, pem


def test_signed_url_carries_params_and_a_valid_signature():
    key, pem = _make_key()
    signer = CloudFrontSigner("KEYID123", pem)
    resource = "https://cdn.example.com/media/report.pdf"

    signed = signer.signed_url(resource, expires_at=2000000000)

    assert signed.startswith(resource + "?")
    params = parse_qs(urlsplit(signed).query)
    assert params["Key-Pair-Id"] == ["KEYID123"]
    assert params["Expires"] == ["2000000000"]

    policy = json.dumps(
        {"Statement": [{"Resource": resource, "Condition": {"DateLessThan": {"AWS:EpochTime": 2000000000}}}]},
        separators=(",", ":"),
    ).encode()
    signature = base64.b64decode(params["Signature"][0].translate(_CLOUDFRONT_B64_REVERSE))
    key.public_key().verify(signature, policy, padding.PKCS1v15(), hashes.SHA1())  # noqa: S303  # raises if invalid


def test_storage_url_signs_with_cloudfront_when_configured():
    _, pem = _make_key()
    storage = S3Storage(bucket="b", custom_domain="cdn.example.com", cloudfront_key=pem, cloudfront_key_id="KEYID123")
    url = storage.url("media/a.txt")
    assert url.startswith("https://cdn.example.com/media/a.txt?")
    assert "Key-Pair-Id=KEYID123" in url


def test_storage_url_custom_domain_unsigned_without_key():
    storage = S3Storage(bucket="b", custom_domain="cdn.example.com")
    assert storage.url("media/a.txt") == "https://cdn.example.com/media/a.txt"
