""" ฅ^•ﻌ•^ฅ """

from argparse import ArgumentParser
from contextlib import asynccontextmanager
from functools import partial
import dataclasses
import json
import orjson
import math
from pprint import pformat

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import JSONResponse as JsonResponse, Response

import httpx

import sqlalchemy as sa

import trio

import materialist.core
from materialist import logging
from materialist.core import (
    exception_handlers,
    HTTP_BAD_REQUEST,
    HTTP_TOO_MANY_REQUESTS,
    MILLIS,
)
from materialist.misc import itemsetter, ritemgetter, linear_lookup

log = logging.getLogger("materialist.steamapi")

MAX_FILE_IDS = 100
PUBLISHEDFILEIDSKEYS = list(f"publishedfileids[{i}]" for i in range(MAX_FILE_IDS))

_get_publishedfiledetails = ritemgetter("response", "publishedfiledetails")
_get_collectiondetails = ritemgetter("response", "collectiondetails")


async def fanout_steamapi_response(request_fn, batch):
    try:
        result = await request_fn()

    except Exception as err:
        for _, reply in batch:
            try:
                reply.send_nowait(err)
            except (trio.EndOfChannel, trio.ClosedResourceError):
                pass
        raise

    else:
        for _, reply in batch:
            try:
                reply.send_nowait(result)
            except (trio.EndOfChannel, trio.ClosedResourceError):
                pass


async def steamapi_GetPublishedFileDetails(client, workshopids):
    url = "https://api.steampowered.com/ISteamRemoteStorage/GetPublishedFileDetails/v1/"

    if len(workshopids) > MAX_FILE_IDS:
        raise ValueError(f"{len(workshopids)} exceedes the limit of {MAX_FILE_IDS}")

    form = dict(zip(PUBLISHEDFILEIDSKEYS, workshopids))
    form["itemcount"] = str(len(workshopids))
    log.butt(form)

    return form

    response = await client.post(url, data=form)
    log.butt(response)

    body = await response.aread()

    return body


@dataclasses.dataclass
class BatchClient(object):
    # steamapi: 'Any'
    items: trio.MemorySendChannel[str]

    async def workshop_item(self, itemid: str):
        # todo use 0 buffer size if we can receive before items.send and
        # guarantee no race/lost messages when the sender is using send_nowait
        reply_s, reply_r = trio.open_memory_channel(1)
        with reply_r:
            await self.items.send((itemid, reply_s))
            return await reply_r.receive()


# def isinstance_or_raise(v, inst):
#     if not isinstance(v, inst):
#         raise ValueError("expected %r, got %r" % (inst, v))
#     return v


# @dataclasses.dataclass
# class Item:
#     workshopid: str
#     title: str
#     creator: str
#     created_at: int
#     updated_at: int | None
#     consumer_app_id: int

#     @classmethod
#     def from_steamapi(cls, values):
#         """
#         >>> Item.from_steamapi({
#         ...     'consumer_app_id': 602960,
#         ...     'creator': '1111',
#         ...     'creator_app_id': 602960,
#         ...     'publishedfileid': '2222',
#         ...     'result': 1,
#         ...     'subscriptions': 8,
#         ...     'tags': [],
#         ...     'time_created': 1234561234,
#         ...     'title': 'funky-monkey',
#         ...     'visibility': 0,
#         ... })
#         Item(workshopid='2222', title='funky-monkey', creator='1111', created_at=1234561234, updated_at=1234561234, consumer_app_id=602960)
#         """
#         f = linear_lookup(values)
#         return cls(
#             workshopid=isinstance_or_raise(f("publishedfileid"), str),
#             title=isinstance_or_raise(f("title"), str),
#             creator=isinstance_or_raise(f("creator"), str),
#             created_at=isinstance_or_raise(f("time_created"), int),
#             updated_at=isinstance_or_raise(f("time_updated") or f("time_created"), int),
#             consumer_app_id=isinstance_or_raise(f("consumer_app_id"), int),
#         )


# async def steamapi_GetCollectionDetails(client, workshopids, fwd):
#     url = "https://api.steampowered.com/ISteamRemoteStorage/GetCollectionDetails/v1/"

#     if len(workshopids) > MAX_FILE_IDS:
#         raise ValueError(f"{len(workshopids)} exceedes the limit of {MAX_FILE_IDS}")

#     form = dict(zip(PUBLISHEDFILEIDSKEYS, workshopids))
#     form["collectioncount"] = str(len(workshopids))

#     response = await client.post(url, data=form)

#     fwd(_get_collectiondetails(response.json()))


# @dataclasses.dataclass
# class PoolRequestItem:
#     workshopid: str
#     reply: trio.MemorySendChannel[None]


# @dataclasses.dataclass
# class PoolRequestCollection:
#     workshopid: str
#     reply: trio.MemorySendChannel[None]


# @dataclasses.dataclass
# class PoolRequestPlayer:
#     steamid: str
#     reply: trio.MemorySendChannel[None]


# type PoolRequest = PoolRequestItem | PoolRequestCollection | PoolRequestPlayer


# async def steamapi_pool(req_r: trio.MemoryReceiveChannel[PoolRequest]):
#     items: Batch[PoolRequestItem] = Batch()
#     collections: Batch[PoolRequestCollection] = Batch()
#     players: Batch[PoolRequestPlayer] = Batch()

#     batches = (items, collections, players)

#     while True:
#         deadline = min(items.drain_at, collections.drain_at, players.drain_at)

#         log.butt(deadline=deadline)
#         with trio.move_on_at(deadline) as cancel_scope:
#             try:
#                 log.butt("steamapi_pool receiving")
#                 req = await req_r.receive()
#             except (trio.EndOfChannel, trio.ClosedResourceError) as err:
#                 log.butt("steamapi_pool req_r err", err=err)
#                 break

#             if isinstance(req, PoolRequestItem):
#                 items.add(req)

#             else:
#                 raise NotImplementedError(req)

#         current_time = trio.current_time()

#         while i := items.drain(current_time):
#             raise NotImplementedError(i)

#         while i := collections.drain(current_time):
#             raise NotImplementedError(i)

#         while i := players.drain(current_time):
#             raise NotImplementedError(i)


# @dataclasses.dataclass
# class Batch[T]():
#     items: list[T] = dataclasses.field(default_factory=list)
#     _drain_at: float | None = None

#     MANY = MAX_FILE_IDS
#     DEBOUNCE = 150 * MILLIS

#     @property
#     def drain_at(self) -> float:
#         import math

#         return math.inf if self._drain_at is None else self._drain_at

#     def add(self, req: T):
#         self.items.append(req)

#         if self._drain_at is None:
#             self._drain_at = trio.current_time() + self.DEBOUNCE

#     def drain(self, current_time) -> list[T]:
#         """
#         takes and returns the first MANY items if we have at least MANY items
#         or current_time is past the drain_at time
#         """
#         if len(self.items) < self.MANY and current_time < self.drain_at:
#             return []

#         taken = self.items[: self.MANY]

#         self.items = self.items[self.MANY :]

#         if not self.items:
#             self._drain_at = None

#         return taken


async def workshop_item(request):
    try:
        body = await request.json()
    except json.decoder.JSONDecodeError:
        return HTTP_BAD_REQUEST

    if not (itemid := body.get("itemid")):
        return HTTP_BAD_REQUEST

    # reply_s, reply_r = trio.open_memory_channel(1)
    # await request.app.state.pool.send(PoolRequestItem(workshopid=itemid, reply=reply_s))

    with trio.move_on_after(1) as cancel_scope:
        wow = await app.state.client.workshop_item(itemid)
        log.debug(wow=wow)

    if cancel_scope.cancelled_caught:
        return HTTP_TOO_MANY_REQUESTS

    # async with trio.open_nursery() as nursery:
    #     nursery.start_soon(
    #         steamapi_GetPublishedFileDetails,
    #         request.app.state.client,
    #         (itemid,),
    #         itemsetter(results, "item"),
    #     )
    #     # nursery.start_soon(
    #     #     steamapi_GetCollectionDetails,
    #     #     client,
    #     #     (itemid,),
    #     #     itemsetter(results, "collection"),
    #     #     partial(setitem, results, "collection"),
    #     # )

    return Response(str(wow))

    # log.butt(results=pformat(results))

    # return JsonResponse([dataclasses.asdict(i) for i in results["item"]])


async def fanin[
    T
](
    nursery,
    httpx_client,
    req_r: trio.MemoryReceiveChannel[T],
    # req_s: trio.MemorySendChannel[list[T]],
    many=MAX_FILE_IDS,
    debounce=50 * MILLIS,
):
    if many < 1:
        raise ValueError(many)

    deadline = None
    items: list[T] = []

    while True:
        with trio.move_on_at(deadline or math.inf) as cancel_scope:
            try:
                msg: T = await req_r.receive()
            except trio.EndOfChannel as err:
                return

            log.butt('fanin', msg=msg)
            items.append(msg)

        assert len(items) <= many

        if len(items) == many or cancel_scope.cancelled_caught:
            batch = items
            # send batch?
            # FIXME
            async_request = partial(
                steamapi_GetPublishedFileDetails, httpx_client, [s for s,_ in batch]
            )
            nursery.start_soon(partial(fanout_steamapi_response, async_request, batch))

            items = []

            deadline = None

        if deadline is None and items:
            deadline = trio.current_time() + debounce


@asynccontextmanager
async def lifespan(app):
    from sqlalchemy import URL, create_engine

    args = NotImplemented # This is so stupid because this thing has two different ways of starting up ...

    url = URL.create("sqlite", database="/tmp/materialist.sqlite")
    db = create_engine(url, echo=True)
    log.butt(db)
    app.state.db = db

    items_s, items_r = trio.open_memory_channel(512)
    async with trio.open_nursery() as nursery, items_s, items_r:
        app.state.client = BatchClient(items=items_s)

        async with create_steamapi_httpx_client(args) as httpx_client:

            # hmmm
            nursery.start_soon(partial(fanin, nursery, httpx_client, items_r))

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
    def derp_():
        db = request.app.state.db
        with db.begin() as tx:
            result = tx.execute(sa.text("select date()"))
            log.butt(result.all())

    wat = await trio.to_thread.run_sync(derp_)
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
    args = parser.parse_args()

    log.debug(args)

    trio.run(partial(serve, app, hypercorn_config(config)))


if __name__ == "__main__":
    main()
