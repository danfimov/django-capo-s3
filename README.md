# django-capo-s3

A Django file storage backend for S3-compatible object stores, built on the [capo-s3](https://pypi.org/project/capo-s3/)
client instead of boto3 — a drop-in alternative to `django-storages[s3]`.

## Features

- **Media and static storages** — `S3Storage` for media, plus `S3StaticStorage` (plain) and `S3ManifestStaticStorage`
  (content-hashed names + `staticfiles.json` manifest for cache-busting) for `collectstatic`. The manifest can live in
  the bucket or in a separate storage.
- **URLs** — presigned URLs by default (with per-call `expire` and response overrides like `response_content_disposition`),
  unsigned public URLs, or CloudFront-signed URLs for a custom domain.
- **Uploads** — streaming single-`PUT` uploads that automatically switch to a concurrent multipart transfer above a
  configurable threshold.
- **Transparent gzip** — eligible content types are stored compressed and decompressed on read.
- **Server-side encryption, storage class, cache-control, metadata, ...** — via `object_parameters`, passed straight
  through to the underlying request.
- **Flexible networking options** — custom endpoint (e.g. MinIO), path- or virtual-host addressing, TLS verification and
  custom CA  bundles, connection timeouts, pool size, HTTP/HTTPS/SOCKS5 proxies, and retry attempts.
- **Fully typed** — this is already the bare minimum for new packages.

## Installation

```bash
uv add django-capo-s3   # or: pip install django-capo-s3
```

## Configuration

Register the backend in Django's `STORAGES` setting; everything under `OPTIONS` is passed to the backend:

```python
STORAGES = {
    "default": {
        "BACKEND": "django_capo_s3.S3Storage",
        "OPTIONS": {
            "bucket": "my-bucket",
            "region": "eu-central-1",
            "location": "media",
        },
    },
    "staticfiles": {
        "BACKEND": "django_capo_s3.S3ManifestStaticStorage",
        "OPTIONS": {"bucket": "my-bucket", "location": "static"},
    },
}
```

## Usage

### On a model field

Most apps never touch storage directly. With `STORAGES["default"]` configured (above), `FileField` /
`ImageField` just work — uploads, `.url`, `.size`, and `.open()` all go through the backend.

```python
from django.db import models

class Report(models.Model):
    csv = models.FileField(upload_to="reports/")

report = Report.objects.create(csv=uploaded_file)
report.csv.url            # presigned URL to the object
report.csv.size           # size in bytes
report.csv.open().read()  # file contents
```

### Direct storage access

Grab the configured default storage from the registry and use the `Storage` API directly.

```python
from django.core.files.base import ContentFile
from django.core.files.storage import storages

storage = storages["default"]
name = storage.save("reports/june.csv", ContentFile(b"col1,col2\n"))  # returns the stored name
storage.exists(name)          # True
storage.size(name)            # size in bytes
with storage.open(name) as f:
    data = f.read()
storage.delete(name)          # no error if it's already gone
```

### Download URLs

`url()` is presigned by default. Override the lifetime per call, or add response headers — for example, to
force a browser "Save as" with a filename.

```python
storage.url("reports/june.csv")                # presigned, default lifetime (url_expire)
storage.url("reports/june.csv", expire=60)     # presigned, valid for 60 seconds
storage.url(
    "reports/june.csv",
    parameters={"response_content_disposition": 'attachment; filename="june.csv"'},
)
```

For a public bucket, set `"querystring_auth": False` in `OPTIONS` to get plain, cacheable URLs instead.

### Static files with cache-busting

Point `STORAGES["staticfiles"]` at `S3ManifestStaticStorage`. `collectstatic` then stores each file under a
content-hashed name and `{% static %}` resolves through the manifest, so assets can be served with
long-lived caching. Keep the manifest local so web workers don't fetch it from S3 on startup.

```python
from django.core.files.storage import FileSystemStorage

STORAGES["staticfiles"] = {
    "BACKEND": "django_capo_s3.S3ManifestStaticStorage",
    "OPTIONS": {
        "bucket": "my-bucket",
        "location": "static",
        "manifest_storage": FileSystemStorage(location=BASE_DIR / ".static-manifest"),
    },
}
```

### Serving through a CDN (CloudFront)

Set `custom_domain` for plain CDN URLs, or add a CloudFront key pair to sign them for a private
distribution.

```python
"OPTIONS": {
    "bucket": "my-bucket",
    "custom_domain": "d123.cloudfront.net",
    "cloudfront_key": cloudfront_private_key_pem,  # PEM contents
    "cloudfront_key_id": "K1ABCDEF",
    "url_expire": 300,
}
# storage.url(name) -> https://d123.cloudfront.net/...?Expires=...&Signature=...&Key-Pair-Id=...
```

### Large uploads and gzip

Uploads switch to a concurrent multipart transfer above `multipart_threshold`; text assets can be stored
gzip-compressed and are transparently decompressed on read.

```python
"OPTIONS": {
    "bucket": "my-bucket",
    "multipart_threshold": 32 * 1024 * 1024,  # start multipart at 32 MiB
    "multipart_chunksize": 16 * 1024 * 1024,
    "multipart_concurrency": 8,               # parts uploaded in parallel
    "gzip": True,                             # compress CSS/JS/JSON/... at rest
}
```

### Encryption, storage class, and other object metadata

Whatever `object_parameters` contains is passed straight to each upload — e.g. SSE-KMS plus a storage class
and cache header.

```python
"OPTIONS": {
    "bucket": "my-bucket",
    "default_acl": "private",
    "object_parameters": {
        "server_side_encryption": "aws:kms",
        "ssekms_key_id": "arn:aws:kms:eu-central-1:123456789012:key/abcd-...",
        "storage_class": "STANDARD_IA",
        "cache_control": "max-age=86400",
    },
}
```

### Networking (endpoint tuning, proxies)

Tune timeouts, the connection pool, retries, TLS verification, and proxies as needed.

```python
"OPTIONS": {
    "bucket": "my-bucket",
    "connect_timeout": 5.0,
    "read_timeout": 30.0,
    "max_connections_per_host": 50,
    "retry_max_attempts": 5,
    "verify": "/etc/ssl/certs/internal-ca.pem",   # or False to disable TLS verification
    "proxies": {"https": "http://proxy.internal:8080"},
}
```
