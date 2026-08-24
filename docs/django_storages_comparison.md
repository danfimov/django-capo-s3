---
title: Comparison with django-storages
---

# Comparison with django-storages

[django-storages](https://github.com/jschneier/django-storages) is the de-facto standard and supports many
backends (S3, Azure, Google Cloud, SFTP, Dropbox, …). `django-capo-s3` does **one** thing — S3-compatible
object storage — and does it on the [capo-s3](https://pypi.org/project/capo-s3/) client instead of boto3.

This page compares only the **S3 part** of the two.

## Feature comparison

| | django-capo-s3 | django-storages[s3] |
|---|:---:|:---:|
| Underlying client | capo-s3 (no boto3) | boto3 / botocore |
| Fully typed, strict-mypy clean | ✅ | ⚠️ partial |
| Media + manifest static storages | ✅ | ✅ |
| Presigned GET / PUT / HEAD URLs | ✅ | ✅ |
| Response overrides in presigned URLs | ✅ | ✅ |
| CloudFront-signed URLs | ✅ | ✅ |
| Response overrides signed **into** CloudFront URLs | ✅ | ❌ ([#1342](https://github.com/jschneier/django-storages/issues/1342)) |
| `collectstatic` skips unchanged uploads | ✅ built-in | ❌ (needs `collectfast`, archived) |
| Transparent gzip at rest | ✅ | ✅ |
| Text-mode `open()` of stored files | ✅ | ❌ ([#1526](https://github.com/jschneier/django-storages/issues/1526)) |
| Streaming / concurrent multipart uploads | ✅ | ✅ |
| Bulk delete | ✅ `delete_objects()` | ➖ per-object |
| Multi-region routing helper | ✅ `for_region()` | ➖ manual |
| Path-style public URLs for a separate public host | ✅ | ⚠️ ([#1142](https://github.com/jschneier/django-storages/issues/1142)) |
| Refreshable credentials (IRSA / SSO, per request) | ✅ | ⚠️ ([#1493](https://github.com/jschneier/django-storages/issues/1493)) |
| Backends beyond S3 (Azure, GCS, SFTP, …) | ❌ | ✅ |
| Maturity, adoption, ecosystem | new | very high |

## Which should you use?

- **Use django-storages** if you need non-S3 backends, or you want the most battle-tested, widely-deployed option.
- **Use django-capo-s3** if you're S3-only and want a fully-typed backend without the boto3 dependency, with
  the S3-specific rough edges below already handled.

## We went through their S3 issues

Before building this, we read the open S3 issues in django-storages. Several of them either don't reproduce here
(different client, better defaults) or are fixed by design. A few concrete examples:

- **Slow `collectstatic`** ([#1255](https://github.com/jschneier/django-storages/issues/1255),
  [#1561](https://github.com/jschneier/django-storages/issues/1561)) — every asset is re-uploaded on each
  deploy because the mtime heuristic breaks on fresh checkouts. Here `skip_unchanged` (on by default) lists
  the bucket once and skips unchanged objects by ETag, so an unchanged deploy costs no uploads.

- **Text-mode reads of stored files** ([#1526](https://github.com/jschneier/django-storages/issues/1526),
  [#1348](https://github.com/jschneier/django-storages/issues/1348)) — `open(name, "rt")` returns `str`, and
  gzip-compressed objects are transparently decompressed on read.

- **Credentials not refreshed on long-running pods**
  ([#1493](https://github.com/jschneier/django-storages/issues/1493)) — IRSA / web-identity and SSO
  credentials are resolved per request and refreshed on expiry, instead of being frozen at client
  construction.

- **CloudFront can't set `Content-Disposition` dynamically**
  ([#1342](https://github.com/jschneier/django-storages/issues/1342)) — response overrides are signed into the
  CloudFront URL, so they survive to the S3 origin (when the distribution forwards them).

- **MinIO behind a separate public host**
  ([#1142](https://github.com/jschneier/django-storages/issues/1142)) — `custom_domain` + `force_path_style`
  produces path-style public URLs (`https://public-host/bucket/key`) even when the client talks to an internal
  endpoint.

Better defaults also close a class of confusion outright: `listdir` uses `list_objects_v2`, and a missing
`bucket` raises a clear error instead of a cryptic one.

Migrating? The `AWS_*` settings you already have keep working — see
[Configuration](tutorial/configuration.md) for the full mapping and the one default that differs.

> **Note.** Issue numbers refer to django-storages at the time of writing; some may since have been addressed
> upstream. The point isn't that django-storages is broken — it's a great, general library — only that a
> focused S3 backend can sand down these specific edges.
