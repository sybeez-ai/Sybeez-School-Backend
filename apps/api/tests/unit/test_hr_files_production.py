"""Production refuses to boot or store HR files on local disk without a bucket."""

import pytest

from src.core.config import Settings


def test_production_refuses_to_start_without_hr_files_bucket():
    settings = Settings(
        app_env="production",
        jwt_secret="x" * 40,
        ai_service_token="y" * 40,
        core_internal_token="z" * 40,
        database_ssl_mode="require",
        s3_bucket="school-files",
        hr_files_s3_bucket="",
        auth_mode="local",
        jobs_backend="redis",
    )
    with pytest.raises(RuntimeError, match="HR_FILES_S3_BUCKET"):
        settings.validate_runtime()


def test_production_starts_when_hr_files_bucket_is_set():
    settings = Settings(
        app_env="production",
        jwt_secret="x" * 40,
        ai_service_token="y" * 40,
        core_internal_token="z" * 40,
        database_ssl_mode="require",
        s3_bucket="school-files",
        hr_files_s3_bucket="school-hr-files",
        auth_mode="local",
        jobs_backend="redis",
    )
    settings.validate_runtime()


def test_hr_storage_uses_existing_bucket_and_refuses_local_disk_in_production(monkeypatch):
    from src.modules.hr_files import storage

    storage._s3.cache_clear()

    class Fake:
        hr_files_s3_bucket = ""
        s3_bucket = ""
        hr_files_s3_region = ""
        s3_region = "ap-south-1"
        aws_region = "us-east-1"
        hr_files_s3_prefix = "hr-files"
        app_env = "production"

    monkeypatch.setattr(storage, "get_settings", lambda: Fake())
    with pytest.raises(RuntimeError, match="HR_FILES_S3_BUCKET"):
        storage.backend()

    Fake.app_env = "development"
    assert storage.backend().name == "local"

    created: dict = {}

    class Dummy:
        name = "s3"

        def __init__(self, bucket, region, prefix):
            created["bucket"] = bucket
            created["region"] = region
            created["prefix"] = prefix

    monkeypatch.setattr(storage, "S3Backend", Dummy)
    storage._s3.cache_clear()
    Fake.hr_files_s3_bucket = ""
    Fake.s3_bucket = "school-files"
    backend = storage.backend()
    assert backend.name == "s3"
    assert created == {"bucket": "school-files", "region": "ap-south-1", "prefix": "hr-files"}

    storage._s3.cache_clear()
    Fake.hr_files_s3_bucket = "hr-private"
    Fake.hr_files_s3_region = "eu-west-1"
    backend = storage.backend()
    assert created["bucket"] == "hr-private"
    assert created["region"] == "eu-west-1"
