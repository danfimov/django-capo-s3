---
title: Overview
---

# django-capo-s3

A Django file storage backend for S3-compatible object stores, built on the
[capo-s3](https://pypi.org/project/capo-s3/) client instead of boto3 — a drop-in alternative to
`django-storages[s3]`.

## Features

- **No boto3** — built on the lightweight, fully-typed capo-s3 client. The package itself is fully typed and
  strict-mypy clean.
- **Media and static storages** — `S3Storage` for media, plus `S3StaticStorage` (plain) and
  `S3ManifestStaticStorage` (content-hashed names + `staticfiles.json` manifest for cache-busting).
  See [Static files](tutorial/static_files.md).
- **Download URLs** — presigned URLs (with per-call `expire` and response overrides), unsigned public URLs,
  and CloudFront-signed URLs. See [Download URLs](tutorial/download_urls.md).
- **Fast `collectstatic`** — the hashing pass lists the bucket once and skips re-uploading unchanged assets,
  so an unchanged deploy costs no uploads.
- **Streaming uploads** — single-`PUT` uploads that switch to a concurrent multipart transfer above a
  configurable threshold; transparent gzip for eligible content types.
- **Multi-region routing** — `storage.for_region("eu-central-1")` clones the storage for another region at
  runtime, cached per region.
- **Any S3-compatible provider** — AWS, MinIO, DigitalOcean Spaces, Backblaze B2, Cloudflare R2, Yandex,
  Hetzner, Alibaba OSS. See [Different providers](tutorial/different_providers.md).
- **Refreshable credentials** — AWS profiles, SSO, and IRSA / web-identity tokens are resolved per request
  and refreshed on expiry (no more silent failures on long-running pods).

Coming from django-storages? See the [comparison](django_storages_comparison.md).

## Installation

```bash
uv add django-capo-s3   # or: pip install django-capo-s3
```

Python 3.11+ and Django 5.2+ are required.

## Quick start

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

With `STORAGES["default"]` configured, `FileField` / `ImageField` just work — uploads, `.url`, `.size`, and
`.open()` all go through the backend:

```python
from django.db import models


class Report(models.Model):
    csv = models.FileField(upload_to="reports/")


report = Report.objects.create(csv=uploaded_file)
report.csv.url  # presigned URL to the object
report.csv.size  # size in bytes
report.csv.open().read()  # file contents
```

Or use the `Storage` API directly:

```python
from django.core.files.base import ContentFile
from django.core.files.storage import storages

storage = storages["default"]
name = storage.save("reports/june.csv", ContentFile(b"col1,col2\n"))
storage.exists(name)  # True
with storage.open(name) as f:
    data = f.read()
storage.delete(name)  # no error if it's already gone
```

Credentials are read from the storage `OPTIONS`, a named AWS profile (`session_profile`), or the standard AWS
credential chain (environment variables, instance/ECS roles, IRSA) — whichever is configured.
