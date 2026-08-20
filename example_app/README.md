# Example app

A minimal Django project that runs `collectstatic` against a local MinIO through
`S3ManifestStaticStorage` — handy for eyeballing the backend end to end.

## Run

```bash
make run_infra                                        # start MinIO on localhost:9000
uv run python example_app/probe.py ensure-bucket      # create the "example-static" bucket
uv run python example_app/manage.py collectstatic --noinput
```

## What to look for

`probe.py list` prints each stored object with its `last_modified`:

```bash
uv run python example_app/probe.py list
```

Run `collectstatic` a second time without changing anything — the hashed assets and the manifest
keep their original `last_modified`, i.e. nothing is re-uploaded (`skip_unchanged`, on by default).
Change a file and only that asset (plus the manifest) gets a fresh timestamp.
