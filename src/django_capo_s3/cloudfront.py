import base64
import json
from typing import TYPE_CHECKING, cast

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

if TYPE_CHECKING:
    from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey


class CloudFrontSigner:
    """Sign URLs for a CloudFront distribution using a canned policy.

    Holds the distribution's private key and key-pair id, and turns a plain URL into a time-limited signed one.
    """

    def __init__(self, key_id: str, private_key_pem: str) -> None:
        """Load the private key and remember the key-pair id used when signing."""
        self._key_id: str = key_id
        loaded = serialization.load_pem_private_key(private_key_pem.encode(), password=None)
        self._key: RSAPrivateKey = cast("RSAPrivateKey", loaded)

    def signed_url(self, url: str, *, expires_at: int) -> str:
        """Return url signed to stay valid until expires_at (an epoch time in seconds)."""
        policy = json.dumps(
            {"Statement": [{"Resource": url, "Condition": {"DateLessThan": {"AWS:EpochTime": expires_at}}}]},
            separators=(",", ":"),
        ).encode()
        # SHA1 is not our choice: CloudFront's canned-policy signature is defined as RSA-SHA1.
        signature = self._key.sign(policy, padding.PKCS1v15(), hashes.SHA1())  # noqa: S303
        # CloudFront expects a URL-safe base64 variant for the signature.
        encoded = base64.b64encode(signature).decode().translate(str.maketrans("+/=", "-~_"))
        separator = "&" if "?" in url else "?"
        return f"{url}{separator}Expires={expires_at}&Signature={encoded}&Key-Pair-Id={self._key_id}"
