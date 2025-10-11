""" ฅ^•ﻌ•^ฅ 

for development, `python -m hypercorn ... --config ... --reload`

depends on:
  hypercorn[trio]
  starlette
"""


from argparse import ArgumentParser
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
import logging
import json
import shutil
from pprint import pformat
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
        # This is shared with the most so we can remove the downloaded files at
        # runtime. But it does not need to be backed by a disk.
        #
        # FIXME Each steamcmd should have its own SCRATCH_DIR as to not collide
        # if they each download the same thing at the same time for some stupid
        # reason.
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

                try:
                    msg.reply.send_nowait(reply)
                except trio.WouldBlock:
                    continue

                try:
                    # finally rmdir or something
                    await trio.to_thread.run_sync(shutil.rmtree, itempath)
                except OSError as err:
                    logger.warn("failed to clean up itempath %s: %s", itempath, err)

        finally:
            with trio.move_on_after(250 * MILLIS, shield=True):
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


@dataclass
class DownloadRequest(object):
    appid: str
    itemid: str
    reply: trio.MemorySendChannel


async def download(request):
    logger.info("this is an info log")

    try:
        body = await request.json()
    except json.decoder.JSONDecodeError:
        return HTTP_BAD_REQUEST

    # logger.info("req headers » %s", pformat(request.headers))
    logger.info("req body » %s", pformat(body))

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


# this isn't used, if we have multiple steamcmds we might use this to handle failures idk
shutdown = trio.Event()

routes = [Route("/download/", download, methods=["POST"])]

exception_handlers = {
    404: lambda _req, _exc: HTTP_NOT_FOUND,
    500: lambda _req, _exc: HTTP_SERVER_ERROR,
}

app = Starlette(routes=routes, lifespan=lifespan, exception_handlers=exception_handlers)

class config:
    # This corresponds to attributes on hypercorn.config.Config, but we can't
    # actually use an instance of that here for some reason ...
    #
    # Also the attributes here don't match the command line arguments
    # (accesslogs vs --access-logfile) and it won't notify you about using
    # non-existent config options, so be careful

    worker_class = "trio"
    bind = ["127.0.0.1:8888"]
    accesslog = '-'


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    from hypercorn.trio import serve
    from hypercorn.config import Config
    # from hypercorn.__main__ import main as hypercorn_main

    parser = ArgumentParser()
    # parser.add_argument('hypercorn_args', nargs='*')
    args = parser.parse_args()

    logger.debug(args)

    # hypercorn_main(['test:app', '-k', 'trio'] + args.hypercorn_args)

    # raise SystemExit(0)

    config = Config()
    config.worker_class = "trio"
    config.bind = ["127.0.0.1:8888"]
    config.accesslog = '-'

    trio.run(partial(serve, app, config, shutdown_trigger=shutdown.wait))
