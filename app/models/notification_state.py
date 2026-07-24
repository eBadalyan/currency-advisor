from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NotificationState(Base):
    __tablename__ = "notification_states"

    id: Mapped[int] = mapped_column(primary_key=True)
    signal_name: Mapped[str] = mapped_column(String(50), unique=True)
    last_value: Mapped[Decimal] = mapped_column(Numeric(18, 8))
    streak_length: Mapped[int] = mapped_column()
    last_alerted_value: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
