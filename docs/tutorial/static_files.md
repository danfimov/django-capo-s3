---
title: Static files and collectstatic
---

# Static files and collectstatic

There are two static storages:

- **`S3StaticStorage`** — serves files under their plain names. Use it when you don't need content hashing.
- **`S3ManifestStaticStorage`** — stores each file under a content-hashed name (`style.<hash>.css`), rewrites
  references inside CSS/JS to those names, and records a `staticfiles.json` manifest so `{% static %}`
  resolves to the hashed name. This is what you want for cache-busting.

Both default to overwriting files in place (`file_overwrite=True`) and serving **unsigned** URLs
(`querystring_auth=False`) so a CDN or browser can cache them.

## Configuration

```python
from django.core.files.storage import FileSystemStorage

STORAGES = {
    "staticfiles": {
        "BACKEND": "django_capo_s3.S3ManifestStaticStorage",
        "OPTIONS": {
            "bucket": "my-bucket",
            "location": "static",
            # Keep the manifest on the local disk so web workers don't fetch it from S3 on every boot.
            "manifest_storage": FileSystemStorage(location=BASE_DIR / ".static-manifest"),
        },
    },
}
```

Then, as usual:

```bash
python manage.py collectstatic --noinput
```

`{% static "css/app.css" %}` now resolves through the manifest to the hashed object, which you can serve with
long-lived caching (`Cache-Control: max-age=31536000, immutable`).

### Where does the manifest live?

By default the manifest is written to the bucket next to your assets. That means every web worker fetches it
from S3 on startup. Passing `manifest_storage=FileSystemStorage(...)` keeps it on local disk instead — the
manifest is baked into your build/image and read locally, which is faster and avoids an S3 round trip per
process.

## Faster re-deploys

Unchanged assets are not re-uploaded on each deploy — `skip_unchanged` is on by default. See
[Further optimizations](further_optimizations.md#faster-re-deploys-skip_unchanged).

## Plain static storage

If you don't need hashed names (e.g. you version assets some other way):

```python
STORAGES["staticfiles"] = {
    "BACKEND": "django_capo_s3.S3StaticStorage",
    "OPTIONS": {"bucket": "my-bucket", "location": "static"},
}
```
