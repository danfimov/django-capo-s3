---
title: Download URLs
---

# Download URLs

`storage.url(name)` returns a URL for an object. What kind of URL depends on your configuration.

## Presigned URLs (the default)

By default `url()` returns a time-limited presigned URL, valid for `url_expire` seconds (default `3600`):

```python
storage.url("reports/june.csv")  # presigned, default lifetime
storage.url("reports/june.csv", expire=60)  # presigned, valid for 60 seconds
```

### Response overrides

Pass `parameters` to override response headers — for example, to force a browser "Save as" with a filename:

```python
storage.url(
    "reports/june.csv",
    parameters={"response_content_disposition": 'attachment; filename="june.csv"'},
)
```

Any `response_*` override is supported: `response_content_type`, `response_content_disposition`,
`response_content_encoding`, `response_content_language`, `response_cache_control`, `response_expires`.

### Presigned uploads

Sign a `PUT` so a browser can upload straight to S3 without proxying bytes through your app:

```python
put_url = storage.url("uploads/photo.jpg", http_method="PUT", expire=300)
# The client does: PUT put_url  with the file as the body.
```

`http_method="HEAD"` is supported too.

### Presigned POST forms

A presigned `PUT` trusts whatever the client sends. `presigned_post()` instead signs a policy that S3 checks on
upload, so the browser can't send a 2 GB file or an HTML page where you expected an avatar:

```python
post = storage.presigned_post(
    "avatars/42.png",
    expire=300,                # defaults to url_expire
    max_size=5 * 1024 * 1024,  # S3 rejects anything larger
)
post["url"]     # where the form posts to
post["fields"]  # hidden fields to send ahead of the file
```

The browser sends every entry of `fields` and then the file itself, last, under the name `file`:

```html
<form action="{{ post.url }}" method="post" enctype="multipart/form-data">
  {% for name, value in post.fields.items %}
    <input type="hidden" name="{{ name }}" value="{{ value }}">
  {% endfor %}
  <input type="file" name="file">
  <button>Upload</button>
</form>
```

The object lands at the same key `save()` would use (the `location` prefix included), and the form pins the
`Content-Type` resolved the way `save()` resolves it, plus `default_acl` when one is set. Pass `fields` to pin
something else — a `Content-Type` there replaces the resolved one:

```python
storage.presigned_post("exports/june", fields={"Content-Type": "text/csv", "Cache-Control": "no-cache"})
```

To let the uploader choose within limits, add `conditions`. A condition on `Content-Type` (or `acl`) turns off
the default for that field, so the browser's own value goes through — as long as it matches:

```python
storage.presigned_post(
    "avatars/42",
    conditions=[{"type": "starts-with", "field": "Content-Type", "prefix": "image/"}],
)
```

`object_parameters` and gzip are not applied to these uploads: the bytes go from the browser to S3 without passing
through the storage, so send any extra headers you need through `fields`.

## Unsigned public URLs

For a public bucket, set `querystring_auth=False` to get plain, cacheable URLs instead of presigned ones:

```python
"OPTIONS": {"bucket": "my-bucket", "querystring_auth": False}
```

```python
storage.url("photo.jpg")  # -> https://my-bucket.s3.eu-central-1.amazonaws.com/photo.jpg
```

## Serving through a CDN (CloudFront)

Set `custom_domain` for plain CDN URLs, or add a CloudFront key pair to sign them for a private distribution:

```python
"OPTIONS": {
    "bucket": "my-bucket",
    "custom_domain": "d123.cloudfront.net",
    "cloudfront_key": cloudfront_private_key_pem,  # PEM contents
    "cloudfront_key_id": "K1ABCDEF",
    "url_expire": 300,
}
```

```python
storage.url("report.pdf")
# -> https://d123.cloudfront.net/report.pdf?Expires=...&Signature=...&Key-Pair-Id=...
```

Response overrides work through CloudFront too — they're signed **into** the URL so they can't be tampered
with. The distribution must be configured to forward those query strings to the S3 origin:

```python
storage.url(
    "report.pdf",
    parameters={"response_content_disposition": 'attachment; filename="report.pdf"'},
)
```

## Public URLs for a self-hosted store (MinIO)

When the app connects to the store at an internal `endpoint` (e.g. `http://minio:9000` inside Docker) but
browsers must reach a different public host, set `custom_domain` to the public host. With `force_path_style`
the bucket goes into the URL path, matching how path-style S3 serves it:

```python
"OPTIONS": {
    "bucket": "my-bucket",
    "endpoint": "http://minio:9000",    # where the app connects
    "custom_domain": "localhost:9000",  # where browsers connect
    "force_path_style": True,
    "url_protocol": "http",
    "querystring_auth": False,
}
```

```python
storage.url("photo.jpg")  # -> http://localhost:9000/my-bucket/photo.jpg
```

See [Different providers](different_providers.md) for full per-provider setups.
