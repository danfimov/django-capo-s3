from pathlib import Path

from capo_s3 import Credentials

BASE_DIR = Path(__file__).resolve().parent

SECRET_KEY = "example-app-not-a-secret"  # noqa: S105
DEBUG = True
INSTALLED_APPS = ["django.contrib.staticfiles"]
USE_TZ = True

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

_S3_OPTIONS = {
    "bucket": "example-static",
    "endpoint": "http://localhost:9000",
    "region": "us-east-1",
    "force_path_style": True,
    "credentials": Credentials(access_key="minioadmin", secret_key="minioadmin"),
    "location": "static",
}

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django_capo_s3.S3ManifestStaticStorage",
        "OPTIONS": _S3_OPTIONS,
    },
}
