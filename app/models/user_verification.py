from sqlalchemy import Column, String, Boolean, DateTime, Enum
from app.core.database import Base
from app.core.timezone import get_ist_now


class UserVerification(Base):
    __tablename__ = "user_verification"

    user_id = Column(String(36), primary_key=True)
    is_verified = Column(Boolean, nullable=False, default=False)
    verified_at = Column(DateTime)
    verification_method = Column(
        Enum("otp", name="verification_method_enum"),
        nullable=False,
        default="otp",
    )
    created_at = Column(DateTime, default=get_ist_now, nullable=False)
    updated_at = Column(DateTime, default=get_ist_now, onupdate=get_ist_now, nullable=False)
