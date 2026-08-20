"""Tiny helper to bootstrap the bucket and snapshot object metadata for the example app.

Usage:
    uv run python example_app/probe.py ensure-bucket
    uv run python example_app/probe.py list
"""

import sys

from capo_s3 import Credentials, S3Client
from capo_s3.errors import NotFound, UnknownServiceError

BUCKET = "example-static"
CLIENT = S3Client(
    endpoint="http://localhost:9000",
    region="us-east-1",
    force_path_style=True,
    credentials=Credentials(access_key="minioadmin", secret_key="minioadmin"),
)


def ensure_bucket() -> None:
    try:
        CLIENT.head_bucket(BUCKET)
        print(f"bucket {BUCKET!r} already exists")
    except (NotFound, UnknownServiceError):  # MinIO answers a missing bucket with a bodyless 404
        CLIENT.create_bucket(BUCKET)
        print(f"created bucket {BUCKET!r}")


def list_objects() -> None:
    result = CLIENT.list_objects_v2(BUCKET, prefix="static/")
    rows = sorted(result.get("contents", []), key=lambda o: o["key"])
    for obj in rows:
        print(f"{obj['last_modified'].isoformat()}  {obj.get('size'):>7}  {obj['key']}")
    print(f"-- {len(rows)} objects")


if __name__ == "__main__":
    {"ensure-bucket": ensure_bucket, "list": list_objects}[sys.argv[1]]()
