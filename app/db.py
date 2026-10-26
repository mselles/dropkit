from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from app import config

_kwargs: dict = {}
if config.DATABASE_URL.startswith("sqlite"):
    _kwargs["connect_args"] = {"check_same_thread": False}
    if ":memory:" in config.DATABASE_URL:
        _kwargs["poolclass"] = StaticPool  # one shared in-memory DB (used by tests)

engine = create_engine(config.DATABASE_URL, **_kwargs)


def create_db() -> None:
    from app import models  # noqa: F401  (registers tables)

    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    with Session(engine) as session:
        yield session
