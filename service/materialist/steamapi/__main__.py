""" ฅ^•ﻌ•^ฅ """

from argparse import ArgumentParser
from contextlib import asynccontextmanager
from functools import partial
import dataclasses
import math

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import JSONResponse as JsonResponse, Response

import orjson

import trio  # slowish import

import httpx  # slow import

import sqlalchemy as sa

import materialist.core
from materialist import logging
from materialist.core import (
    exception_handlers,
    HTTP_BAD_REQUEST,
    HTTP_TOO_MANY_REQUESTS,
    MILLIS,
    BAROTRAUMA_APPID,
    current_timestamp,
)
from materialist.misc import itemsetter, itemgetter, ritemgetter, linear_lookup
from materialist.steamapi import schema
from materialist.steamapi.model import WorkshopItemVersion, PlayerVersion
from materialist.data import open_sqlite

log = logging.getLogger("materialist.steamapi")

MAX_FILE_IDS = 100
PUBLISHEDFILEIDSKEYS = list(f"publishedfileids[{i}]" for i in range(MAX_FILE_IDS))

_get_publishedfiledetails = ritemgetter("response", "publishedfiledetails")
_get_collectiondetails = ritemgetter("response", "collectiondetails")
_get_playersummaries = ritemgetter("response", "players")


async def steamapi_GetPublishedFileDetails(client, workshopids):
    url = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"

    if len(workshopids) > MAX_FILE_IDS:
        raise ValueError(f"{len(workshopids)} exceedes the limit of {MAX_FILE_IDS}")

    form = dict(zip(PUBLISHEDFILEIDSKEYS, workshopids))
    form["itemcount"] = str(len(workshopids))

    response = await client.post(url, data=form)

    body = await response.aread()

    # body = open("/tmp/GetPublishedFileDetails.json").read()

    deser = orjson.loads(body)
    results = _get_publishedfiledetails(deser)

    results = sorted(results, key=lambda r: workshopids.index(r["publishedfileid"]))

    if any(r["publishedfileid"] != i for r, i in zip(results, workshopids)):
        raise ValueError(
            "publishedfiledetails have unexpected ordering", (workshopids, results)
        )

    return results


async def steamapi_GetPlayerSummaries(client, steamids):
    url = "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/"

    if len(steamids) > MAX_FILE_IDS:
        raise ValueError(f"{len(workshopids)} exceedes the limit of {MAX_FILE_IDS}")

    query = [
        ("key", app.state.args.steamapikey),
        ("steamids", ",".join(steamids)),
    ]

    response = await client.get(url, params=query)

    body = await response.aread()

    deser = orjson.loads(body)
    results = _get_playersummaries(deser)

    results = sorted(results, key=lambda r: steamids.index(r["steamid"]))

    if any(r["steamid"] != i for r, i in zip(results, steamids)):
        raise ValueError(
            "players have unexpected ordering", (steamids, results)
        )

    return results


type AsyncEnvelope[T, R] = tuple[T, R]
type SteamApiAsyncEnvelope = AsyncEnvelope[str, dict]


@dataclasses.dataclass
class SteamApiEnvelopeClient(object):
    items: trio.MemorySendChannel[SteamApiAsyncEnvelope]
    players: trio.MemorySendChannel[SteamApiAsyncEnvelope]

    @staticmethod
    async def _send_and_receive_using(s: trio.MemorySendChannel[SteamApiAsyncEnvelope], v: str):
        # todo use 0 buffer size if we can receive before items.send and
        # guarantee no race/lost messages when the sender is using send_nowait
        reply_s, reply_r = trio.open_memory_channel(1)
        with reply_r:
            await s.send((v, reply_s))
            return await reply_r.receive()

    async def workshop_item(self, workshopid: str) -> dict:
        return await self._send_and_receive_using(self.items, workshopid)

    async def player(self, steamid: str):
        return await self._send_and_receive_using(self.players, steamid)



async def workshop_item(request):
    """ """
    try:
        body = await request.json()
    except json.decoder.JSONDecodeError:
        return HTTP_BAD_REQUEST

    if not (itemid := body.get("itemid")):
        return HTTP_BAD_REQUEST

    with trio.move_on_after(1) as cancel_scope:
        itemdict: dict = await app.state.client.workshop_item(itemid)

    if cancel_scope.cancelled_caught:
        return HTTP_TOO_MANY_REQUESTS

    item = WorkshopItemVersion.from_steamapi(itemdict)

    if item.workshopid != itemid:
        raise ValueError("WorkshopItemVersion workshopid unexpected", itemid, item)

    if item.consumer_app_id != BAROTRAUMA_APPID:
        return Response("item %s has appid %s, barotrauma's appid is %s" % (itemid, item.consumer_app_id,BAROTRAUMA_APPID), status_code=400)

    playerdict = await app.state.client.player(item.author)

    player = PlayerVersion.from_steamapi(playerdict)

    if player.steamid != item.author:
        raise ValueError("PlayerVersion steamid unexpected", item.author, player)

    async with app.state.db_lock:
        # FIXME run this in the io thread thingy whatever
        with log.clocked("workshop_item db phase"):
            with app.state.db.begin() as tx:
                ts = data.create_timestamp(tx)
                raise NotImplementedError

    # log.debug(wow=wow)

    return Response(str("???"))



async def fanin[
    T
](
    req_r: trio.MemoryReceiveChannel[T],
    req_s: trio.MemorySendChannel[list[T]],
    many=MAX_FILE_IDS,
    debounce=150 * MILLIS,
):
    if many < 1:
        raise ValueError(many)

    deadline = None
    items: list[T] = []

    while True:
        log.butt("fanin", deadline=deadline, items=items)

        with trio.move_on_at(deadline or math.inf) as cancel_scope:
            try:
                msg: T = await req_r.receive()
            except trio.EndOfChannel as err:
                log.exc("fanin quitting", err=err)
                return

            items.append(msg)

        assert len(items) <= many

        if len(items) == many or cancel_scope.cancelled_caught:
            try:
                await req_s.send(items)
            except trio.BrokenResourceError as err:
                log.exc("fanin quitting", err=err)
                return

            items = []
            deadline = None

        elif deadline is None and items:
            deadline = trio.current_time() + debounce


async def fanout(
    batch_r: trio.MemoryReceiveChannel[list[SteamApiAsyncEnvelope]],
    fn,
):
    while True:
        async with trio.open_nursery() as nursery:
            try:
                batch = await batch_r.receive()
            except trio.EndOfChannel:
                log.exc("fanout quitting", err=err)
                return

            log.butt("fanout", fn=fn, batch=batch)

            args, reply_senders = zip(*batch)

            try:
                results = await fn(args)

            except Exception as err:
                log.exception("fanout fn exception", fn=fn, args=args, err=err)

            else:
                log.butt("fanout results", results=results)

                for sender, result in zip(reply_senders, results):
                    try:
                        sender.send_nowait(result)
                    except trio.ClosedResourceError:
                        pass

            finally:
                for s in reply_senders:
                    s.close()


@asynccontextmanager
async def lifespan(app):
    args = app.state.args

    app.state.db = open_sqlite(args.db, echo=True)
    app.state.db_lock = trio.Lock()

    log.butt('w00t')

    items_s, items_r = trio.open_memory_channel(512)
    players_s, players_r = trio.open_memory_channel(512)
    manyitems_s, manyitems_r = trio.open_memory_channel(3)
    manyplayers_s, manyplayers_r = trio.open_memory_channel(3)
    async with (
        trio.open_nursery() as nursery,
        create_steamapi_httpx_client(args) as httpx_client,
        items_s,
        items_r,
        players_s,
        players_r,
        manyitems_s,
        manyitems_r,
        manyplayers_s,
        manyplayers_r,
    ):
        app.state.client = SteamApiEnvelopeClient(items=items_s, players=players_s)

        nursery.start_soon(partial(fanin, items_r, manyitems_s))
        nursery.start_soon(partial(fanin, players_r, manyplayers_s))

        manyitems_fn = partial(steamapi_GetPublishedFileDetails, httpx_client)
        nursery.start_soon(partial(fanout, manyitems_r, manyitems_fn))

        manyplayers_fn = partial(steamapi_GetPlayerSummaries, httpx_client)
        nursery.start_soon(partial(fanout, manyplayers_r, manyplayers_fn))

        yield


@asynccontextmanager
async def create_steamapi_httpx_client(args):
    limits = httpx.Limits(
        max_connections=9, keepalive_expiry=90, max_keepalive_connections=3
    )
    client = httpx.AsyncClient(http2=True, limits=limits, trust_env=False)
    async with client as client:
        yield client


async def dbtest(request):
    def derp_(db):
        with db.begin() as tx:
            result = tx.execute(sa.text("select date()"))
            log.butt(result.all())

    async with app.state.db_lock:
        wat = await trio.to_thread.run_sync(derp_, request.app.state.db)
        log.butt(wat)

    return JsonResponse(wat)


async def sleep(request):
    log.butt("sleeping")
    await trio.sleep(100 * MILLIS)
    return Response("ฅ^•ﻌ•^ฅ")


routes = [
    Route("/workshop-item/", workshop_item, methods=["POST"]),
    Route("/test/", dbtest),
    Route("/sleep/", sleep),
]

app = Starlette(routes=routes, lifespan=lifespan, exception_handlers=exception_handlers)


class config(materialist.core.config):
    bind = ["127.0.0.1:8888"]


def main():
    logging.basicConfig(level=logging.DEBUG)

    from materialist.core import hypercorn_config
    from hypercorn.trio import serve

    parser = ArgumentParser()
    parser.add_argument("--db", default=':memory:')
    parser.add_argument("--db-echo", default=False, action='store_true')
    parser.add_argument("--steamapikey")
    # parser.add_argument("hypercorn_args", nargs="*")
    args = parser.parse_args()

    log.debug(args)

    app.state.args = args

    # not supported along with app.state.args ....
    # if args.hypercorn_args:
    #     import hypercorn.__main__

    #     log.warn("starting SILLY MODE ~ passing arguments to hypercorn.__main__")

    #     hypercorn.__main__.main(
    #             ["materialist.steamapi:app", "--config", "python:materialist.steamapi.config"]
    #         + args.hypercorn_args
    #     )

    # else:

    trio.run(partial(serve, app, hypercorn_config(config)))


if __name__ == "__main__":
    main()
