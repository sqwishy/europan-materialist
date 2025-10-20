from dataclasses import dataclass

from sqlalchemy import URL, create_engine
from sqlalchemy import select, text, func, update
from sqlalchemy.dialects.sqlite import insert # for on_conflict_do_update

from materialist.core import current_timestamp
from materialist.misc import sliced_at
from materialist.steamapi import schema as s
from materialist.steamapi.model import WorkshopItemVersion, PlayerVersion


@dataclass
class VersionResult():
    root_pk: int
    pk: int
    is_new_version: bool
    

def open_sqlite(path, **kwargs):
    url = URL.create("sqlite", database=path)
    db = create_engine(url, **kwargs)

    # conditional by default, will not recreate tables already present
    s.meta.create_all(db)

    with db.begin() as tx:
        r = tx.execute(select(text('count(*)')).select_from(s.clock))
        if not r.scalar_one():
            tx.execute(insert(s.clock).values())

    return db
 

def create_timestamp(tx):
    q = update(s.clock) \
        .values(ts=func.max(s.clock.c.ts + 1, current_timestamp())) \
        .returning(s.clock.c.ts)
    return tx.execute(q).scalar_one()


def _version_query(table, lineage, *matches):
    q = select(func.max(table.c.ts),
               table.c.pk) \
        .where(lineage) \
        .cte()
    q = select(table.c.pk) \
        .select_from(q) \
        .where(table.c.pk == q.c.pk) \
        .where(*matches)
    return q


@dataclass
class PlayerVersionResult():
    steamid: str
    title: str | None
    urL: str | None
    
    
@dataclass
class WorkshopItemVersionResult():
    title: str
    created_at: int
    updated_at: int
    author: PlayerVersionResult
    

def query_workshop_item(tx, workshopid: str) -> WorkshopItemVersionResult | None:
    # q = select(s.workshop_item.pk) \
    #     .where(s.workshop_item.workshopid=workshopid) \
    #     .cte()
    q = select(s.workshop_item_version.c.title,
               s.workshop_item_version.c.created_at,
               s.workshop_item_version.c.updated_at,
               s.player.c.steamid,
               s.player_version.c.name,
               s.player_version.c.url) \
        .where(s.workshop_item.c.workshopid==workshopid,
               s.workshop_item.c.pk==s.workshop_item_version.c.workshopid,
               s.player.c.pk==s.workshop_item_version.c.author) \
        .join(s.player_version, s.player.c.pk==s.player_version.c.steamid, isouter=True)
               # s.player.c.pk==s.workshop_item_version.c.author,
               # s.player.c.pk==s.player_version.c.steamid)
    # return tx.execute(q).one_or_none()
    if (result := tx.execute(q).one_or_none()) is None:
        return None

    args, rest = sliced_at(result, 3)
    return WorkshopItemVersionResult(*args, author=PlayerVersionResult(*rest))


def maybe_insert_player_version(tx, ts: int, player: PlayerVersion) -> VersionResult:
    # upsert player steamid
    # do update does nothing here, but allows returning the pk
    q = insert(s.player) \
        .values(steamid=player.steamid) \
        .on_conflict_do_update(index_elements=("steamid",), set_={"pk": text("pk")}) \
        .returning(s.player.c.pk)
    player_pk = tx.execute(q).scalar_one()

    # check if the most recent version for this player is different enough
    q = _version_query(s.player_version,
                       s.player_version.c.steamid == player_pk,
                       s.player_version.c.name == player.name,
                       s.player_version.c.url == player.url)
    player_version_pk = tx.scalars(q).one_or_none()
    
    if player_version_pk is not None:
        return VersionResult(root_pk=player_pk, pk=player_version_pk, is_new_version=False)

    q = insert(s.player_version) \
        .values(ts=ts,
                steamid=player_pk,
                name=player.name,
                url=player.url) \
        .returning(s.player.c.pk)
    return VersionResult(root_pk=player_pk, pk=tx.execute(q).scalar_one(), is_new_version=True)


def maybe_insert_workshop_item_version(tx, ts, player_pk: int, item: WorkshopItemVersion) -> VersionResult:
    q = insert(s.workshop_item) \
        .values(workshopid=item.workshopid) \
        .on_conflict_do_update(index_elements=("workshopid",), set_={"pk": text("pk")}) \
        .returning(s.workshop_item.c.pk)
    workshop_item_pk = tx.execute(q).scalar_one()

    q = _version_query(s.workshop_item_version,
                       s.workshop_item_version.c.workshopid == workshop_item_pk,
                       s.workshop_item_version.c.updated_at == item.updated_at)
    workshop_item_version_pk = tx.scalars(q).one_or_none()

    if workshop_item_version_pk is not None:
        return VersionResult(root_pk=workshop_item_pk, pk=workshop_item_version_pk, is_new_version=False)

    q = insert(s.workshop_item_version) \
        .values(ts=ts,
                workshopid=workshop_item_pk,
                title=item.title,
                author=player_pk,
                created_at=item.created_at,
                updated_at=item.updated_at) \
        .returning(s.workshop_item_version.c.pk)
    return VersionResult(root_pk=workshop_item_pk, pk=tx.execute(q).scalar_one(), is_new_version=True)


def test_insert_version():
    """ ensure only actually new "versions" are inserted """
    db = open_sqlite(":memory:")

    with db.begin() as tx:
        ts = create_timestamp(tx)

        player = PlayerVersion(steamid='1', name='A', url='')
        assert 1 == maybe_insert_player_version(tx, ts, player).pk
    
        player = PlayerVersion(steamid='2', name='B', url='')
        assert 2 == maybe_insert_player_version(tx, ts, player).pk

    with db.begin() as tx:
        ts = create_timestamp(tx)

        player = PlayerVersion(steamid='1', name='A', url='')
        assert 1 == maybe_insert_player_version(tx, ts, player).pk

        player = PlayerVersion(steamid='1', name='B', url='')
        assert 3 == maybe_insert_player_version(tx, ts, player).pk

    with db.begin() as tx:
        ts = create_timestamp(tx)

        player = PlayerVersion(steamid='1', name='A', url='')
        assert 4 == maybe_insert_player_version(tx, ts, player).pk

        player = WorkshopItemVersion(workshopid='W', title='A', author=NotImplemented,
                                     created_at=123, updated_at=123, consumer_app_id=69)
        assert 1 == maybe_insert_workshop_item_version(tx, ts, 4, player).pk
