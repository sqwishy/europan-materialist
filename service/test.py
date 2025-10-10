""" ฅ^•ﻌ•^ฅ """

from argparse import ArgumentParser
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
import logging
from pprint import pformat
from signal import raise_signal, SIGINT
from pathlib import Path
from subprocess import CompletedProcess, CalledProcessError

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import Response, JSONResponse as JsonResponse

import trio

# from aiohttp import web

logger = logging.getLogger(__name__)

MILLIS = 1.0 / 1000.0

# RUN_DIR = Path("/var/run/materialist-steamcmd")
RUN_DIR = Path("/tmp/materialist-steamcmd")
SCRATCH_DIR = RUN_DIR / "scratch"

HTTP_BAD_REQUEST = Response("bad request", status_code=400)
HTTP_NOT_FOUND = Response("not found", status_code=404)
HTTP_TOO_MANY_REQUESTS = Response(
    "this computer too busy (x . x) ~~zzZ", status_code=429
)
HTTP_SERVER_ERROR = Response(
    "something went fucky wucky on the computer doing the website and the thing didn't work, sorry",
    status_code=500,
)


# def ready_and_get(readyq, oneq):
#     """ returns None if the queue shutdown """
#     try:
#         readyq.put_nowait(oneq)
#     except asyncio.QueueShutDown:
#         return None
#     else:
#         return oneq.get()


# async def start_workers(app):
#     app["readyq"] = readyq = Queue()

#     sigint_sent = False

#     def first_sigint():
#         nonlocal sigint_sent

#         if sigint_sent:
#             sigint_sent = True
#             return True

#         else:
#             return False


#     for _ in range(app["args"].nsteamcmds):
#         oneq = Queue(maxsize=1)
#         task = create_task(run_one_steamcmd(readyq, oneq))
#         task.add_done_callback(lambda _: readyq.shutdown())
#         task.add_done_callback(lambda _: first_sigint() and raise_signal(SIGINT))

#     yield

#     readyq.shutdown()

#     # drain the readyq of parked guys and let them shut down
#     try:
#         while q := readyq.get_nowait():
#             q.put_nowait(None)
#     except asyncio.QueueShutDown:
#         pass

#     await task


# async def run_one_steamcmd(readyq, oneq):
#     args = ["podman", "run", "--rm", "-i",
#             # "-v", "scratch:/"
#             "steamcmd/steamcmd:latest",
#             # "+force_install_dir", "/tmp/bind",
#             "+login anonymous"]
#     p = await create_subprocess_exec(*args, stdin=PIPE, stdout=PIPE, stderr=STDOUT)

#     buf = deque(maxlen=64)

#     async def read_lines():
#         assert p.stdout # pyright

#         while (b := await p.stdout.read(64)):
#             logger.info("steamcmd » %s", b)
#             buf.extend(b)

#             if bytes(buf).endswith(b'Steam>\x1b[0m'):
#                 pass

#         # hmmm...

#     async def read_queue():

#         while req := await ready_and_get(readyq, oneq):
#             logger.info("queue » %s", req)

#         assert p.stdin is not None # pyright

#         logger.info("quitting")
#         p.stdin.write(b"quit\n")
#         p.stdin.close()

#     # "download_item", "602960", workshopid

#     # tar -cv --zstd

#     async with asyncio.TaskGroup() as tg:
#         # if steamcmd exits, read_lines will exit, we will need to unpark ...
#         tg.create_task(read_lines())
#         queue_task = tg.create_task(read_queue())
#         await p.wait()
#         queue_task.cancel() # ?????????????

#     logger.info("run_one_steamcmd is quitting")


# async def download_request(request):
#     body = await request.json()

#     if not (appid := body.get('appid')):
#         return web.HTTPBadRequest()

#     if not (itemid := body.get('itemid')):
#         return web.HTTPBadRequest()

#     # logger.info("\n%s", pformat(body))

#     # breakpoint()

#     try:
#         oneq = request.app['readyq'].get_nowait()
#     except (asyncio.QueueEmpty, asyncio.QueueShutDown):
#         return web.HTTPTooManyRequests()

#     await oneq.put(DownloadRequest(appid=appid, itemid=itemid))

#     return web.Response(text="(っ◔◡◔)っ")


class SteamApiOutputBuf(object):
    READYBYTES = b"\nSteam>\x1b[0m"

    def __init__(self, stdout):
        self.buf = deque(maxlen=64)
        self.stdout = stdout

    def is_ready_for_command(self, buf=b""):
        self.buf.extend(buf)
        return bytes(self.buf).endswith(self.READYBYTES)

    async def read_until_ready(self):
        """ This will block if you call it while ready! """
        while (b := await self.stdout.receive_some()):

            self.buf.extend(b)
            if bytes(self.buf).endswith(self.READYBYTES):
                return True

        return False


async def run_one_steamcmd(req_r, *, task_status=trio.TASK_STATUS_IGNORED):
    from subprocess import PIPE, STDOUT

    args = [
        "podman",
        "run",
        "--rm",
        "-i",
        "-v",
        f"{SCRATCH_DIR}:/root/.local/share/Steam/steamcmd/linux32/steamapps/content",
        "steamcmd/steamcmd:alpine",
        # "+force_install_dir", "/tmp/bind",
        "+login anonymous",
    ]

    async with trio.open_nursery() as nursery:
        steamcmd = await nursery.start(
            partial(trio.run_process, args, stdin=PIPE, stdout=PIPE, stderr=STDOUT)
        )

        task_status.started(steamcmd)

        buf = SteamApiOutputBuf(steamcmd.stdout)

        try:
            if not await buf.read_until_ready():
                logger.warn("steamcmd EOF before startup")
            else:
                logger.info("steamcmd ready")

            while True:

                # if this is closed, our service is shutting down
                try:
                    msg: DownloadRequest = await req_r.receive()
                except (trio.EndOfChannel, trio.ClosedResourceError):
                    logger.warn("steamcmd req_r channel closed")
                    break

                await steamcmd.stdin.send_all(
                    b"download_item %s %s\n" % (msg.appid.encode(), msg.itemid.encode())
                )

                if not await buf.read_until_ready():
                    logger.warn("steamcmd EOF during download_item")
                    break

                itempath = SCRATCH_DIR / f'app_{msg.appid}' / f'item_{msg.itemid}'
                tar_args = ['tar', '-c', '--zstd', '-C', itempath, '.']
                try:
                    result: CompletedProcess = await trio.run_process(tar_args, capture_stdout=True, capture_stderr=True)
                except CalledProcessError as err:
                    logger.warn("steamcmd tar failed", err)
                    reply = ":<"
                else:
                    reply = result.stdout

                # finally rmdir or something

                try:
                    msg.reply.send_nowait(reply)
                except trio.WouldBlock:
                    continue
        finally:
            with trio.move_on_after(50 * MILLIS, shield=True):
                try:
                    await steamcmd.stdin.send_all(b"\nquit\n")
                except (trio.BrokenResourceError, trio.ClosedResourceError):
                    pass

            with trio.move_on_after(3, shield=True):
                r = await steamcmd.wait()
                logger.info("steamcmd exit » %s", r)

            logger.info("%s", bytes(buf.buf))

        logger.info("steamcmd done!")

    logger.info("steamcmd nursery closed!")


# async def run_one_steamcmd(req_r, *, task_status=trio.TASK_STATUS_IGNORED):
#     task_status.started()

#     async for msg in req_r:
#         logger.info("steamcmd » %s", msg)
#         try:
#             msg.reply.send_nowait("ฅ^•ﻌ•^ฅ")
#         except trio.WouldBlock:
#             continue


@asynccontextmanager
async def lifespan(app):
    logger.info("lifespan starting up")

    RUN_DIR.mkdir(mode=0o770, exist_ok=True)
    SCRATCH_DIR.mkdir(exist_ok=True)

    async with trio.open_nursery() as nursery:

        req_s, req_r = trio.open_memory_channel(0)
        with req_s, req_r:
            app.state.req_s = req_s
            app.state.req_r = req_r

            r = await nursery.start(run_one_steamcmd, req_r)
            logger.info("background » %s", r)
            logger.info("lifespan up")
            try:
                yield
            finally:
                await r.stdin.send_all(b"quit\n")
                logger.info("lifespan down")

        logger.info("req_r %s, req_s %r", req_r, req_s)


async def download(request):
    body = await request.json()

    logger.info("req body %s", pformat(body))

    if not (appid := body.get("appid")):
        return HTTP_BAD_REQUEST

    if not (itemid := body.get("itemid")):
        return HTTP_BAD_REQUEST

    reply_s, reply_r = trio.open_memory_channel(0)

    async with reply_r, reply_s:
        msg = DownloadRequest(appid=appid, itemid=itemid, reply=reply_s)

        try:
            request.app.state.req_s.send_nowait(msg)
        except trio.WouldBlock:
            return HTTP_TOO_MANY_REQUESTS

        woot = await reply_r.receive()

        # with trio.move_on_after(1) as cancel_scope:
        #     woot = await reply_r.receive()

        # if cancel_scope.cancelled_caught

    return Response(woot)


@dataclass
class DownloadRequest(object):
    appid: str
    itemid: str
    reply: trio.MemorySendChannel


shutdown = trio.Event()

routes = [Route("/download/", download, methods=["POST"])]

exception_handlers = {
    404: lambda _req, _exc: HTTP_NOT_FOUND,
    500: lambda _req, _exc: HTTP_SERVER_ERROR,
}

app = Starlette(routes=routes, lifespan=lifespan, exception_handlers=exception_handlers)


logging.basicConfig(level=logging.DEBUG)
if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    from hypercorn.trio import serve
    from hypercorn.config import Config

    config = Config()

    # config.bind = ["127.0.0.1:"]
    config.worker_class = "trio"
    # config.graceful_timeout = 1234

    trio.run(partial(serve, app, config, shutdown_trigger=shutdown.wait))
