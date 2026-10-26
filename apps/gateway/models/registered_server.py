from sqlalchemy import Boolean, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base
from models.mixins import TimestampMixin


class RegisteredServer(TimestampMixin, Base):
    __tablename__ = "registered_servers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    server_type: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    is_enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
    # Fernet ciphertext for optional upstream Authorization header. Never expose.
    auth_header_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
