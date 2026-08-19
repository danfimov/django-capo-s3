from capo_s3 import Credentials

SECRET_KEY = "django-capo-s3-tests"  # noqa: S105
USE_TZ = True
INSTALLED_APPS: list[str] = []
DATABASES: dict[str, object] = {}

DEFAULT_TEST_BUCKET = "django-capo-s3-default"  # Bucket that the STORAGES-registration test creates/cleans up itself

STORAGES = {
    "default": {
        "BACKEND": "django_capo_s3.storage.S3Storage",
        "OPTIONS": {
            "bucket": DEFAULT_TEST_BUCKET,
            "endpoint": "http://localhost:9000",
            "region": "us-east-1",
            "force_path_style": True,
            "credentials": Credentials(access_key="minioadmin", secret_key="minioadmin"),  # noqa: S106
        },
    },
}
