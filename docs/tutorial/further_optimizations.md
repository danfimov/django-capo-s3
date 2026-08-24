---
title: Further optimizations
---

# Further optimizations

Everything here is optional. The defaults are already the fast path for most projects — reach for these when
deploys are slow or when a service moves enough bytes that the HTTP layer starts to show up in profiles.

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

See [Static files](static_files.md) for the rest of the `collectstatic` setup.

## A Rust HTTP backend (`pyreqwest`)

Requests go through [zapros](https://zapros.dev), which defaults to a pure-Python HTTP stack. zapros can also
run on [pyreqwest](https://zapros.dev/rust.html), a Rust implementation — worth switching to when you move
enough bytes that HTTP parsing shows up in a profile, since request handling drops out of Python entirely.

```bash
uv add "zapros[pyreqwest]"   # or: pip install "zapros[pyreqwest]"
```

Installing it is not enough — zapros won't pick the Rust backend up on its own. Name the client builder and the
handler that wraps it; the storage's transport options keep working as they always did:

```python
from pyreqwest.client import SyncClientBuilder
from zapros import PyreqwestHandler

STORAGES = {
    "default": {
        "BACKEND": "django_capo_s3.S3Storage",
        "OPTIONS": {
            "bucket": "my-bucket",
            "region": "eu-central-1",
            "http_client_builder": SyncClientBuilder,
            "http_handler": PyreqwestHandler,
            "connect_timeout": 2.0,
            "read_timeout": 30.0,
            "max_connections_per_host": 16,
        },
    },
}
```

The storage calls `http_client_builder()` for a fresh builder, applies each transport option by calling the
matching method on it, and hands the result to `http_handler`. Both are called on first use rather than at
settings import, so no connection pool is created before a `--preload` fork, and the resulting handler is
shared by object requests and credential resolution alike — one pool for everything.

Options are applied best-effort, since builders differ in what they expose:

- a builder without the matching method logs a warning and is skipped — `write_timeout` on reqwest, for
  instance, which bounds the whole request with `total_timeout` instead;
- `proxies` and `verify` need a transport-specific object (a proxy builder, a parsed certificate), so they are
  left to the builder — set them with `.proxy(...)`, `.danger_accept_invalid_certs(...)` or
  `.add_root_certificate_pem(...)` and pass a pre-configured builder;
- durations arrive as `timedelta`, not seconds.

Any builder and handler pair works the same way, so this is not specific to pyreqwest. To reach settings the
storage options don't model, hand over a builder that already carries them:

```python
"http_client_builder": lambda: SyncClientBuilder().http2_prior_knowledge(),
```
