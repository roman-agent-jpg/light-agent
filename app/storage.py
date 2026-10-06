"""Облачное хранилище Storj (S3-совместимое, 25 ГБ).

Используется boto3 с кастомным endpoint. Файл хранится в оперативной памяти,
потоковое чтение не требуется — наш пейлоад небольшой.
"""
import io

from . import config

_client = None
_unavailable = False


def _get_client():
    global _client, _unavailable
    if _unavailable:
        return None
    if _client is not None:
        return _client
    if not config.storage_enabled():
        _unavailable = True
        return None
    try:
        import boto3
        from botocore.config import Config as BotoConfig

        _client = boto3.client(
            "s3",
            endpoint_url=config.S3_ENDPOINT,
            aws_access_key_id=config.S3_ACCESS_KEY,
            aws_secret_access_key=config.S3_SECRET_KEY,
            region_name=config.S3_REGION,
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path"},  # Storj требует path-style
                retries={"max_attempts": 3},
                # botocore >= 1.36 по умолчанию включает aws-chunked с
                # x-amz-sdk-checksum-algorithm. Storj gateway не понимает
                # chunked-тело и не видит Content-Length -> 400
                # MissingContentLength. Отключаем, шлём обычный Content-Length.
                request_checksum_calculation="when_required",
            ),
        )
        return _client
    except Exception:
        _unavailable = True
        return None


def ensure_bucket() -> bool:
    """Создаёт бакет, если не существует."""
    client = _get_client()
    if client is None:
        return False
    try:
        client.head_bucket(Bucket=config.S3_BUCKET)
        return True
    except Exception:
        try:
            client.create_bucket(Bucket=config.S3_BUCKET)
            return True
        except Exception:
            return False


def upload_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> bool:
    client = _get_client()
    if client is None:
        return False
    try:
        # Storj требует явный Content-Length, иначе 400 MissingContentLength.
        # Именно bytes, а не BytesIO: boto3 посчитает длину сам и отправит заголовок.
        client.put_object(
            Bucket=config.S3_BUCKET, Key=key,
            Body=data, ContentLength=len(data), ContentType=content_type,
        )
        return True
    except Exception:
        return False


def upload_file(key: str, path: str) -> bool:
    client = _get_client()
    if client is None:
        return False
    try:
        client.upload_file(path, config.S3_BUCKET, key)
        return True
    except Exception:
        return False


def download_bytes(key: str) -> bytes | None:
    client = _get_client()
    if client is None:
        return None
    try:
        obj = client.get_object(Bucket=config.S3_BUCKET, Key=key)
        return obj["Body"].read()
    except Exception:
        return None


def list_keys(prefix: str = "", limit: int = 100) -> list[dict]:
    client = _get_client()
    if client is None:
        return []
    try:
        resp = client.list_objects_v2(
            Bucket=config.S3_BUCKET, Prefix=prefix, MaxKeys=limit
        )
        return [
            {"key": o["Key"], "size": o["Size"],
             "modified": o["LastModified"].isoformat()}
            for o in resp.get("Contents", [])
        ]
    except Exception:
        return []


def delete_key(key: str) -> bool:
    client = _get_client()
    if client is None:
        return False
    try:
        client.delete_object(Bucket=config.S3_BUCKET, Key=key)
        return True
    except Exception:
        return False


def presigned_url(key: str, expires: int = 3600) -> str | None:
    """Временная ссылка на скачивание."""
    client = _get_client()
    if client is None:
        return None
    try:
        return client.generate_presigned_url(
            "get_object",
            Params={"Bucket": config.S3_BUCKET, "Key": key},
            ExpiresIn=expires,
        )
    except Exception:
        return None


def status() -> dict:
    return {
        "enabled": _get_client() is not None,
        "service": "storj-s3",
        "bucket": config.S3_BUCKET if config.storage_enabled() else None,
    }