"""Проверка облачного хранилища Storj (S3-совместимое).

Ключи берутся из переменных окружения, в коде их нет.
Проверяет доступ, создание бакета, запись, чтение и удаление.

Запуск (PowerShell):
  $env:S3_ACCESS_KEY="..."; $env:S3_SECRET_KEY="..."
  python test_storage.py
"""
import os
import sys
import uuid

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

ENDPOINT = os.getenv("S3_ENDPOINT", "https://gateway.storjshare.io")
ACCESS = os.getenv("S3_ACCESS_KEY", "")
SECRET = os.getenv("S3_SECRET_KEY", "")
BUCKET = os.getenv("S3_BUCKET", "agent-files")
REGION = os.getenv("S3_REGION", "us-east-1")

ok = True


def show(text: str) -> None:
    """Печать без зависимости от кодировки консоли.

    Консоль PowerShell часто не в UTF-8, и кириллица превращается
    в мусор. В utf-8 файл пишем нормально, в консоль - ASCII-безопасный вид.
    """
    print(text.encode("ascii", "backslashreplace").decode("ascii"))


def step(label: str, fn):
    global ok
    try:
        show(f"[OK]   {label}: {fn()}")
    except ClientError as e:
        ok = False
        err = e.response.get("Error", {})
        show(f"[FAIL] {label}: ClientError "
             f"{err.get('Code', '?')}: {err.get('Message', str(e))[:200]}")
    except Exception as e:
        ok = False
        show(f"[FAIL] {label}: {type(e).__name__}: {str(e)[:200]}")


if not ACCESS or not SECRET:
    show("S3_ACCESS_KEY / S3_SECRET_KEY are not set in environment.")
    sys.exit(2)

s3 = boto3.client(
    "s3",
    endpoint_url=ENDPOINT,
    aws_access_key_id=ACCESS,
    aws_secret_access_key=SECRET,
    region_name=REGION,
    config=BotoConfig(
        signature_version="s3v4",
        s3={"addressing_style": "path"},
        retries={"max_attempts": 3},
        # см. app/storage.py: без этого Storj не получает Content-Length
        request_checksum_calculation="when_required",
    ),
)

show(f"endpoint={ENDPOINT} bucket={BUCKET} region={REGION}\n")

step("list_buckets", lambda: [b["Name"] for b in s3.list_buckets()["Buckets"]])


def make_bucket():
    try:
        s3.create_bucket(Bucket=BUCKET)
        return f"created {BUCKET}"
    except ClientError as e:
        if e.response.get("Error", {}).get("Code") in (
            "BucketAlreadyExists", "BucketAlreadyOwnedByYou",
        ):
            return f"{BUCKET} already exists"
        raise


step("create_bucket", make_bucket)

key = f"diag/{uuid.uuid4().hex[:8]}.txt"
payload = ("Проверка связи со Storj. " * 3).encode("utf-8")

step("put_object", lambda: s3.put_object(
    Bucket=BUCKET, Key=key, Body=payload,
    ContentLength=len(payload), ContentType="text/plain",
) and f"{len(payload)} bytes -> {key}")


def get_object():
    got = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
    return f"read {len(got)} bytes, exact_match={got == payload}"


step("get_object", get_object)
step("presigned_url", lambda: s3.generate_presigned_url(
    "get_object", Params={"Bucket": BUCKET, "Key": key}, ExpiresIn=3600
)[:80] + "...")
step("delete_object", lambda: s3.delete_object(Bucket=BUCKET, Key=key) and "deleted")

show("\nRESULT: all operations passed" if ok
     else "\nRESULT: failures above")
sys.exit(0 if ok else 1)