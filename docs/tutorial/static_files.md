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

## Faster re-deploys (`skip_unchanged`)

Django's `collectstatic` decides whether to re-upload a file by comparing modification times. On a fresh
checkout (CI, a new container image) every source file has a brand-new mtime, so **every asset is
re-uploaded on every deploy** — often minutes of needless traffic.

`S3ManifestStaticStorage` fixes this with `skip_unchanged` (**on by default**): during the hashing pass it
lists the bucket once and skips uploading any object whose content already matches what's stored (compared by
ETag). An unchanged deploy costs **zero uploads**.

```python
"OPTIONS": {
    "bucket": "my-bucket",
    "location": "static",
    "skip_unchanged": True,  # the default; set to False to fall back to Django's behaviour
}
```

What it does under the hood, scoped to the `collectstatic` run only:

- one `list_objects_v2` to build a `{key: ETag}` index instead of a `HEAD` per file;
- skips the `PUT` when the content hash matches the stored ETag;
- skips the redundant delete-before-overwrite (S3 `PUT` overwrites anyway);
- doesn't upload the intermediate hashed files each pass would otherwise write.

The skip is by **content**, not mere existence: if the object at a key has different bytes, it is re-uploaded.

Turn it off (`"skip_unchanged": False`) if your S3-compatible store computes ETags in a way that isn't a
content MD5 — then the comparison can't match and you'd re-upload every time anyway.

> **Multipart and SSE.** Very large assets stored as multipart uploads, or objects encrypted with SSE-KMS,
> have ETags that aren't a plain content MD5. Those simply don't match and are re-uploaded — never wrongly
> skipped.

## Plain static storage

If you don't need hashed names (e.g. you version assets some other way):

```python
STORAGES["staticfiles"] = {
    "BACKEND": "django_capo_s3.S3StaticStorage",
    "OPTIONS": {"bucket": "my-bucket", "location": "static"},
}
```
