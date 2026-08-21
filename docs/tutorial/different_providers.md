---
title: Different providers
---

# Different providers

Anything that speaks the S3 API works — you just point `endpoint`, `region`, and `force_path_style` at the
right values. The snippets below are starting points; adjust the region and host for your account.

## Credentials

You can pass credentials three ways:

- **Environment** (recommended) — set `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` and leave them out of
  `OPTIONS`. This works for every provider, not just AWS.
- **Explicitly** in `OPTIONS`:

    ```python
    import os
    from capo_s3 import Credentials

    CREDENTIALS = Credentials(
        access_key=os.environ["S3_ACCESS_KEY"],
        secret_key=os.environ["S3_SECRET_KEY"],
    )
    # ... "credentials": CREDENTIALS
    ```

- **A named AWS profile** — `"session_profile": "my-profile"` (resolves `role_arn` / SSO / web-identity too).

Only `bucket` is required in `OPTIONS`; everything else falls back to a default.

## Amazon S3

```python
"OPTIONS": {
    "bucket": "my-bucket",
    "region": "eu-central-1",
}
```

Running on EKS with IRSA, or on EC2/ECS instance roles? Credentials are resolved per request and refreshed on
expiry automatically — nothing to configure.

## MinIO (self-hosted)

```python
"OPTIONS": {
    "bucket": "my-bucket",
    "endpoint": "http://minio:9000",
    "region": "us-east-1",       # any value MinIO is configured with
    "force_path_style": True,
    "credentials": CREDENTIALS,
}
```

If browsers reach MinIO at a different host than your app does, add a public host for URL generation — see
[Public URLs for a self-hosted store](download_urls.md#public-urls-for-a-self-hosted-store-minio).


## Hetzner Object Storage

You can use something like this as options in your Django project:

```python
"OPTIONS": {
    "bucket": "django-capo-s3",
    "endpoint": "https://hel1.your-objectstorage.com",
    "region": "hel1",  # or fsn1 / nbg1
}
```

For more information please refer to [Hetzner's documentation about S3](https://docs.hetzner.com/storage/object-storage/getting-started/using-libraries#for-python). Their examples use `boto3` with an explicit `Config` — every option there maps to a capo-s3 default, so you don't need any of it:

```python
from capo_s3 import Credentials, S3Client

s3 = S3Client(
    endpoint="https://hel1.your-objectstorage.com",
    region="hel1",
    credentials=Credentials(access_key="YOUR_ACCESS_KEY", secret_key="YOUR_SECRET_KEY"),
)
```

- `signature_version='s3v4'` — SigV4 is the default signer.
- `addressing_style='virtual'` — virtual-host addressing is the default (`force_path_style=False`), so requests
  go to `https://<bucket>.hel1.your-objectstorage.com`. boto3 only needs this override because it switches to
  path-style when `endpoint_url` is set; capo-s3 doesn't.
- `payload_signing_enabled=False` — uploads are streamed and signed with `UNSIGNED-PAYLOAD` already.

> **Note.** This one is verified against a real Hetzner bucket — save, read, `listdir`, and presigned URLs
> all work out of the box.

## Alibaba Cloud OSS

Object Storage Service exposes an S3-compatible endpoint and they claim that [AWS SDK can be used for accessing it](https://www.alibabacloud.com/help/en/oss/developer-reference/use-aws-sdks-to-access-oss):

```python
"OPTIONS": {
    "bucket": "django-capo-s3",
    "endpoint": "https://s3.oss-eu-west-1.aliyuncs.com",   # UK (London); other regions: s3.oss-<region>.aliyuncs.com
    "region": "eu-west-1",
}
```

Alibaba's own boto3 example signs with `signature_version='s3'` — that's **SigV2**, the legacy AWS signature —
but the S3-compatible endpoint also accepts **SigV4**, which is what capo-s3 uses. No extra configuration needed.

> **Note.** Verified against a real Alibaba OSS bucket (UK / London) — save, read, `listdir`, presigned URLs,
> and delete all work over SigV4.

## Yandex Object Storage

```python
"OPTIONS": {
    "bucket": "django-capo-s3",
    "endpoint": "https://storage.yandexcloud.net",
    "region": "ru-central1",
}
```

> **Note.** Verified against a real Yandex Object Storage bucket — save, read, `listdir`, presigned URLs, and
> delete all work out of the box.

## DigitalOcean Spaces

```python
"OPTIONS": {
    "bucket": "my-space",
    "endpoint": "https://nyc3.digitaloceanspaces.com",
    "region": "nyc3",
    "default_acl": "public-read",     # Spaces honours canned ACLs
    "custom_domain": "my-space.nyc3.cdn.digitaloceanspaces.com",  # optional CDN endpoint
}
```

## Backblaze B2

```python
"OPTIONS": {
    "bucket": "my-bucket",
    "endpoint": "https://s3.us-west-002.backblazeb2.com",
    "region": "us-west-002",
}
```

## Cloudflare R2

```python
"OPTIONS": {
    "bucket": "my-bucket",
    "endpoint": "https://<accountid>.r2.cloudflarestorage.com",
    "region": "auto",
    "force_path_style": True,
    "custom_domain": "cdn.example.com",  # your R2 custom domain, for public URLs
    "querystring_auth": False,
}
```

## Path-style vs virtual-host

Most providers accept both addressing styles. `force_path_style=True` sends requests as
`https://host/bucket/key` (needed by MinIO and a few others); the default is virtual-host style
`https://bucket.host/key`. If uploads work but URLs 404 (or vice versa), try flipping `force_path_style`.
