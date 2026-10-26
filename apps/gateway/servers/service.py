"""CRUD for RegisteredServer records."""

from __future__ import annotations

from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth.exceptions import NotFoundError, ValidationError
from models.registered_server import RegisteredServer
from servers.credentials import encrypt_authorization_header

ALLOWED_SERVER_TYPES = frozenset({"mcp", "streamable-http"})
_MAX_NAME_LENGTH = 128
_MAX_URL_LENGTH = 2048


def normalize_server_name(name: str) -> str:
    cleaned = (name or "").strip().lower()
    if not cleaned:
        raise ValidationError("Server name is required.")
    if len(cleaned) > _MAX_NAME_LENGTH:
        raise ValidationError(f"Server name must be at most {_MAX_NAME_LENGTH} characters.")
    if not cleaned.replace("-", "").replace("_", "").isalnum():
        raise ValidationError(
            "Server name may only contain letters, digits, hyphens, and underscores."
        )
    return cleaned


def normalize_server_type(server_type: str) -> str:
    cleaned = (server_type or "").strip().lower()
    if cleaned not in ALLOWED_SERVER_TYPES:
        raise ValidationError(
            "Invalid server_type. Allowed values: mcp, streamable-http."
        )
    return cleaned


def normalize_endpoint_url(endpoint_url: str) -> str:
    cleaned = (endpoint_url or "").strip()
    if not cleaned:
        raise ValidationError("endpoint_url is required.")
    if len(cleaned) > _MAX_URL_LENGTH:
        raise ValidationError(
            f"endpoint_url must be at most {_MAX_URL_LENGTH} characters."
        )
    parsed = urlparse(cleaned)
    if parsed.scheme not in {"http", "https"}:
        raise ValidationError("endpoint_url must use http or https.")
    if not parsed.netloc:
        raise ValidationError("endpoint_url must include a host.")
    return cleaned


def _apply_optional_credentials(
    record: RegisteredServer,
    *,
    authorization_header: str | None,
    clear_credentials: bool,
) -> None:
    if clear_credentials:
        record.auth_header_ciphertext = None
        return
    if authorization_header is None:
        return
    record.auth_header_ciphertext = encrypt_authorization_header(authorization_header)


def create_registered_server(
    session: Session,
    *,
    name: str,
    server_type: str,
    endpoint_url: str,
    is_enabled: bool = True,
    authorization_header: str | None = None,
) -> RegisteredServer:
    record = RegisteredServer(
        name=normalize_server_name(name),
        server_type=normalize_server_type(server_type),
        endpoint_url=normalize_endpoint_url(endpoint_url),
        is_enabled=bool(is_enabled),
    )
    _apply_optional_credentials(
        record,
        authorization_header=authorization_header,
        clear_credentials=False,
    )
    session.add(record)
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ValidationError(
            "A registered server with this name already exists."
        ) from exc
    session.refresh(record)
    return record


def list_registered_servers(session: Session) -> list[RegisteredServer]:
    return list(
        session.scalars(
            select(RegisteredServer).order_by(
                RegisteredServer.name.asc(), RegisteredServer.id.asc()
            )
        ).all()
    )


def get_registered_server(session: Session, server_id: int) -> RegisteredServer:
    record = session.get(RegisteredServer, server_id)
    if record is None:
        raise NotFoundError("Registered server not found.")
    return record


def update_registered_server(
    session: Session,
    server_id: int,
    *,
    name: str | None = None,
    server_type: str | None = None,
    endpoint_url: str | None = None,
    is_enabled: bool | None = None,
    authorization_header: str | None = None,
    clear_credentials: bool = False,
) -> RegisteredServer:
    record = get_registered_server(session, server_id)
    if name is not None:
        record.name = normalize_server_name(name)
    if server_type is not None:
        record.server_type = normalize_server_type(server_type)
    if endpoint_url is not None:
        record.endpoint_url = normalize_endpoint_url(endpoint_url)
    if is_enabled is not None:
        record.is_enabled = bool(is_enabled)
    _apply_optional_credentials(
        record,
        authorization_header=authorization_header,
        clear_credentials=clear_credentials,
    )
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise ValidationError(
            "A registered server with this name already exists."
        ) from exc
    session.refresh(record)
    return record


def delete_registered_server(session: Session, server_id: int) -> None:
    record = get_registered_server(session, server_id)
    session.delete(record)
    try:
        session.commit()
    except Exception:
        session.rollback()
        raise


def list_enabled_servers(session: Session) -> list[RegisteredServer]:
    return list(
        session.scalars(
            select(RegisteredServer)
            .where(RegisteredServer.is_enabled.is_(True))
            .order_by(RegisteredServer.name.asc())
        ).all()
    )
