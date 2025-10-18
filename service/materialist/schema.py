from sqlalchemy import (
    MetaData,
    Table,
    Column,
    Integer,
    String as Text,
    BLOB as Blob,
    ForeignKey,
    Index,
    CheckConstraint,
)

meta = MetaData()

# fmt: off
download = Table(
    "download",
    meta,
    Column("pk",        Integer, primary_key=True),
    Column("timestamp", Integer, nullable=False),
    Column("data",      Blob,    nullable=False),
    Column("size",      Integer, nullable=False),
    Column("hash",      Blob,    nullable=False),
)

Index("download/size-hash", download.c.size, download.c.hash, unique=True)

steam_player = Table(
    "steam_player",
    meta,
    Column("pk",        Integer, primary_key=True),
    Column("steamid",   Text,    nullable=False),
)

steam_player_summary = Table(
    "steam_player_summary",
    meta,
    Column("pk",        Integer, ForeignKey("steam_player.pk"), primary_key=True),
    Column("name",      Text,    nullable=False),
    Column("url",       Text,    nullable=False),
)

workshop_item = Table(
    "workshop_item",
    meta,
    Column("pk",         Integer, primary_key=True),
    Column("workshopid", Text,    nullable=False),
)

Index("workshop_item/workshopid", workshop_item.c.workshopid, unique=True)

workshop_item_version = Table(
    "workshop_item_version",
    meta,
    Column("pk",         Integer, primary_key=True),
    Column("workshopid", Integer, ForeignKey("workshopid.pk"), nullable=False),
    Column("timestamp",  Integer, nullable=False),
    Column("title",      Text,    nullable=False),
    Column("author",     Integer, ForeignKey("steam_player.pk"), nullable=False),
    Column("created_at", Integer, nullable=False),
    Column("updated_at", Integer, nullable=False),
    Column("download",   Integer, ForeignKey("download.pk"), nullable=True),
)

# Index("workshop_item_version/workshopid-created_at", workshop_item_version.c.pk, unique=True)

workshop_item_fetch = Table(
    "workshop_item_fetch",
    meta,
    Column("pk",         Integer, primary_key=True),
    Column("workshopid", Text,    nullable=False),
    Column("timestamp",  Integer, nullable=False),
    Column("ok",         Integer, ForeignKey("workshop_item_version.pk"), nullable=True),
    Column("error",      Text, nullable=True),
    CheckConstraint("ok is null or error is null", name="ok and error cannot both be set"),
)
# fmt: on
