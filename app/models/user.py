from sqlalchemy import Column, String, Text, Integer, DateTime, Boolean
from app.core.database import Base
from app.core.timezone import get_ist_now


class User(Base):
    __tablename__ = "users"

    id = Column(String(36), primary_key=True)
    phone_encrypted = Column(Text, nullable=False)
    phone_hash = Column(String(64), nullable=False, unique=True)
    video_count = Column(Integer, default=0, nullable=False)
    # Sticky once accepted: a returning user (same number) gets the consent box
    # pre-ticked on the details form.
    tc_accepted = Column(Boolean, default=False, nullable=False, server_default="0")
    created_at = Column(DateTime, default=get_ist_now, nullable=False)
    updated_at = Column(DateTime, default=get_ist_now, onupdate=get_ist_now, nullable=False)
