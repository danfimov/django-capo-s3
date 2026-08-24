---
title: Configuration
---

# Configuration

Register a backend in Django's `STORAGES` setting. Everything under `OPTIONS` is passed to the storage, and
only `bucket` is required:

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

## Where a value comes from

Each option is resolved in this order, first hit winning:

1. the storage's own `OPTIONS`;
2. a matching `AWS_*` setting, for compatibility with django-storages;
3. the built-in default.

`OPTIONS` is the recommended place — it keeps two storages pointing at different buckets independent, which a
global setting cannot. The settings layer exists so a project migrating off django-storages doesn't have to
move its configuration in the same change as the backend swap.

Credentials are the exception worth stating plainly: leave them unset and capo resolves them the way the AWS
tooling does — environment variables, `~/.aws/credentials`, a container role, IRSA, SSO. You only need the
`credentials` option (or the key settings below) when the keys live somewhere capo won't look.

## Settings read for django-storages compatibility

| Setting                                               | Option                                         |
| ----------------------------------------------------- | ---------------------------------------------- |
| `AWS_STORAGE_BUCKET_NAME`                             | `bucket`                                       |
| `AWS_LOCATION`                                        | `location`                                     |
| `AWS_S3_ENDPOINT_URL`                                 | `endpoint`                                     |
| `AWS_S3_REGION_NAME`                                  | `region`                                       |
| `AWS_S3_ADDRESSING_STYLE`                             | `force_path_style` (`"path"` → `True`)         |
| `AWS_QUERYSTRING_AUTH`                                | `querystring_auth`                             |
| `AWS_QUERYSTRING_EXPIRE`                              | `url_expire`                                   |
| `AWS_S3_CUSTOM_DOMAIN`                                | `custom_domain`                                |
| `AWS_S3_URL_PROTOCOL`                                 | `url_protocol` (the trailing colon is dropped) |
| `AWS_CLOUDFRONT_KEY`, `AWS_CLOUDFRONT_KEY_ID`         | `cloudfront_key`, `cloudfront_key_id`          |
| `AWS_S3_FILE_OVERWRITE`                               | `file_overwrite`                               |
| `AWS_DEFAULT_ACL`                                     | `default_acl`                                  |
| `AWS_IS_GZIPPED`                                      | `gzip`                                         |
| `GZIP_CONTENT_TYPES`                                  | `gzip_content_types`                           |
| `AWS_S3_OBJECT_PARAMETERS`                            | `object_parameters`                            |
| `AWS_S3_MAX_MEMORY_SIZE`                              | `max_memory_size`                              |
| `AWS_S3_SESSION_PROFILE`                              | `session_profile`                              |
| `AWS_S3_PROXIES`                                      | `proxies`                                      |
| `AWS_S3_VERIFY`                                       | `verify` (`None` means "use the default")      |
| `AWS_S3_ACCESS_KEY_ID` or `AWS_ACCESS_KEY_ID`         | `credentials` (with the secret below)          |
| `AWS_S3_SECRET_ACCESS_KEY` or `AWS_SECRET_ACCESS_KEY` | `credentials`                                  |
| `AWS_SESSION_TOKEN` or `AWS_SECURITY_TOKEN`           | `credentials`                                  |

Both halves of a key pair have to be present before `credentials` is built at all — a lone access key is
ignored rather than half-configuring the client, so the ambient chain still gets its chance.

The static storages pin `file_overwrite=True` and `querystring_auth=False` regardless of these settings.
Hashed asset names are meant to be overwritten in place and served unsigned, so those two aren't negotiable.

`AWS_S3_SIGNATURE_VERSION` is not read: capo signs with SigV4 only. Neither are `AWS_S3_USE_SSL` (put the
scheme in `endpoint`), `AWS_S3_FILE_NAME_CHARSET`, `AWS_S3_TRANSFER_CONFIG`, `AWS_S3_CLIENT_CONFIG` or
`AWS_S3_USE_THREADS` — see [Further optimizations](further_optimizations.md) for the options that replace the
last three.

## Options with no django-storages equivalent

`skip_unchanged`, `default_content_type`, `multipart_threshold`, `multipart_chunksize`,
`multipart_concurrency`, `connect_timeout`, `read_timeout`, `write_timeout`, `total_timeout`,
`max_connections_per_host`, `retry_max_attempts`, `http_client_builder` and `http_handler` are set through
`OPTIONS` only.

## One default to check when migrating

**`max_memory_size` defaults to 10 MB**, where django-storages uses `0`. An opened file is held in memory up to
that size before spilling to a temporary file, so on a read-heavy path this is real memory per open file. Set
it to `0` to match django-storages and always spill to disk.
