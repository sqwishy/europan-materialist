from sqlalchemy import URL, create_engine
from sqlalchemy import select, text, func, update
from sqlalchemy.dialects.sqlite import insert # for on_conflict_do_update

from materialist.core import current_timestamp
from materialist.steamapi import schema as s
from materialist.steamapi.model import WorkshopItemVersion, PlayerVersion
    

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


def _query_version(table, lineage, *matches):
    q = select(func.max(table.c.ts),
               table.c.pk) \
        .where(lineage) \
        .cte()
    q = select(table.c.pk) \
        .select_from(q) \
        .where(table.c.pk == q.c.pk) \
        .where(*matches)
    return q


def insert_player_version(tx, ts: int, player: PlayerVersion) -> int:
    # upsert player steamid
    # do update does nothing here, but allows returning the pk
    q = insert(s.player) \
        .values(steamid=player.steamid) \
        .on_conflict_do_update(index_elements=("steamid",), set_={"pk": text("pk")}) \
        .returning(s.player.c.pk)
    player_pk = tx.execute(q).scalar_one()

    # check if the most recent version for this player is different enough
    q = _query_version(s.player_version,
                       s.player_version.c.steamid == player_pk,
                       s.player_version.c.name == player.name,
                       s.player_version.c.url == player.url)
    player_version_pk = tx.scalars(q).one_or_none()
    
    if player_version_pk is not None:
        return player_version_pk

    q = insert(s.player_version) \
        .values(steamid=player_pk, ts=ts, name=player.name, url=player.url) \
        .returning(s.player.c.pk)
    return tx.execute(q).scalar_one()


def test_insert_player_version():
    """ ensure only actually new "versions" are inserted """
    db = open_sqlite(":memory:")

    with db.begin() as tx:
        ts = create_timestamp(tx)

        player = PlayerVersion(steamid='1', name='A', url='')
        assert 1 == insert_player_version(tx, ts, player)
    
        player = PlayerVersion(steamid='2', name='B', url='')
        assert 2 == insert_player_version(tx, ts, player)

    with db.begin() as tx:
        ts = create_timestamp(tx)

        player = PlayerVersion(steamid='1', name='A', url='')
        assert 1 == insert_player_version(tx, ts, player)

        player = PlayerVersion(steamid='1', name='B', url='')
        assert 3 == insert_player_version(tx, ts, player)

    with db.begin() as tx:
        ts = create_timestamp(tx)

        player = PlayerVersion(steamid='1', name='A', url='')
        assert 4 == insert_player_version(tx, ts, player)


def insert_workshop_item_version(tx, ts, item: WorkshopItemVersion):
    q = insert(s.workshop_item) \
        .values(workshopid=item.workshopid) \
        .on_conflict_do_update(index_elements=("workshopid",), set_={"pk": text("pk")}) \
        .returning(s.workshop_item.c.pk)
    workshop_item_pk = tx.execute(q).scalar_one()

    q = insert(s.workshop_item_version) \
        .values(timestamp=ts,
                workshopid=workshop_item_pk,
                title=item.title,
                author=item.steam_player_id,
                created_at=item.created_at,
                updated_at=item.updated_at) \
        .on_conflict_do_nothing()
    return tx.execute(q).scalar_one()
    # r = tx.execute(q)
