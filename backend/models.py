"""Database tables. SQLite on a laptop, PostgreSQL on Render."""
from sqlalchemy import Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
import os
from pathlib import Path

class Base(DeclarativeBase):
    pass

class Spot(Base):
    __tablename__ = "spots"
    id: Mapped[str] = mapped_column(String(5), primary_key=True)
    zone: Mapped[str] = mapped_column(String(2))
    kind: Mapped[str] = mapped_column(String(24))
    distance: Mapped[int] = mapped_column(Integer)

class Visit(Base):
    __tablename__ = "visits"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plate: Mapped[str] = mapped_column(String(16), index=True)
    vehicle_type: Mapped[str] = mapped_column(String(24))
    spot_id: Mapped[str] = mapped_column(String(5))
    entered_at: Mapped[str] = mapped_column(String(40))
    exited_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    fee: Mapped[int | None] = mapped_column(Integer, nullable=True)

class WaitItem(Base):
    __tablename__ = "wait_items"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    plate: Mapped[str] = mapped_column(String(16), unique=True)
    vehicle_type: Mapped[str] = mapped_column(String(24))
    queued_at: Mapped[str] = mapped_column(String(40))

class Observation(Base):
    __tablename__ = "observations"
    spot_id: Mapped[str] = mapped_column(String(5), primary_key=True)
    plate: Mapped[str] = mapped_column(String(16))
    seen_at: Mapped[str] = mapped_column(String(40))

class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(30))
    plate: Mapped[str | None] = mapped_column(String(16), nullable=True)
    spot_id: Mapped[str | None] = mapped_column(String(5), nullable=True)
    note: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[str] = mapped_column(String(40))
    snapshot_json: Mapped[str] = mapped_column(Text)

class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[str] = mapped_column(String(200))


def make_session_factory(url=None):
    if url is None:
        default_file = Path(__file__).resolve().parent.parent / 'parking_local.db'
        url = os.getenv('DATABASE_URL') or f'sqlite:///{default_file}'
    if url.startswith('postgres://'):
        url = 'postgresql+psycopg://' + url.removeprefix('postgres://')
    elif url.startswith('postgresql://'):
        url = 'postgresql+psycopg://' + url.removeprefix('postgresql://')
    engine = create_engine(url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)
