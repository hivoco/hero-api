import boto3
from app.core.config import settings

# Local: uses access key from .env
# Production (EC2): uses the IAM role attached to the instance
if settings.AWS_ACCESS_KEY_ID and settings.AWS_SECRET_ACCESS_KEY:
    s3_client = boto3.client(
        "s3",
        region_name=settings.AWS_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )
else:
    s3_client = boto3.client(
        "s3",
        region_name=settings.AWS_REGION,
    )


def upload_fileobj_to_s3(fileobj, key: str, content_type: str) -> str:
    """Upload a file-like object to S3 and return an HTTPS URL."""
    try:
        s3_client.upload_fileobj(
            Fileobj=fileobj,
            Bucket=settings.AWS_S3_BUCKET,
            Key=key,
            ExtraArgs={"ContentType": content_type},
        )
        return f"https://{settings.AWS_S3_BUCKET}.s3.{settings.AWS_REGION}.amazonaws.com/{key}"
    except Exception as e:
        print(f"❌ S3 Upload Error: {str(e)}")
        raise
