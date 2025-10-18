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


clock = Table(
    "clock",
    meta,
    Column("ts",        Integer, primary_key=True),
)


download = Table(
    "download",
    meta,
    Column("pk",        Integer, primary_key=True),
    Column("ts",        Integer, nullable=False),
    Column("data",      Blob,    nullable=False),
    Column("size",      Integer, nullable=False),
    Column("hash",      Blob,    nullable=False),
)

Index("download/size-hash", download.c.size, download.c.hash, unique=True)


workshop_item_download = Table(
    "workshop_item_download",
    meta,
    Column("pk",        Integer, ForeignKey("download.pk"), primary_key=True),
    Column("name",      Text,    nullable=False),
    Column("version",   Text,    nullable=False),
)


player = Table(
    "player",
    meta,
    Column("pk",        Integer, primary_key=True),
    Column("steamid",   Text,    nullable=False),
)

Index("player/steamid", player.c.steamid, unique=True)


player_version = Table(
    "player_version",
    meta,
    Column("pk",         Integer, primary_key=True),
    Column("ts",         Integer, nullable=False),
    Column("steamid",    Integer, ForeignKey("player.pk"), nullable=False),
    Column("name",       Text,    nullable=False),
    Column("url",        Text,    nullable=False),
)

Index("player_version/steamid-ts",       
      player_version.c.steamid,
      player_version.c.ts,
      unique=True)


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
    Column("ts",         Integer, nullable=False),
    Column("workshopid", Integer, ForeignKey("workshop_item.pk"), nullable=False),
    Column("title",      Text,    nullable=False),
    Column("author",     Integer, ForeignKey("player.pk"), nullable=False),
    Column("created_at", Integer, nullable=False),
    Column("updated_at", Integer, nullable=False),
    # Column("download",   Integer, ForeignKey("download.pk"), nullable=True),
)

Index("workshop_item_version/workshopid-updated_at",
      workshop_item_version.c.workshopid,
      workshop_item_version.c.updated_at,
      unique=True)


# workshop_item_fetch = Table(
#     "workshop_item_fetch",
#     meta,
#     Column("pk",         Integer, primary_key=True),
#     Column("workshopid", Text,    nullable=False),
#     Column("ts",         Integer, nullable=False),
#     Column("ok",         Integer, ForeignKey("workshop_item_version.pk"), nullable=True),
#     Column("error",      Text, nullable=True),
#     CheckConstraint("ok is null or error is null", name="ok and error cannot both be set"),
# )
# fmt: on
