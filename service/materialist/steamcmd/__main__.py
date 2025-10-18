""" ฅ^•ﻌ•^ฅ 

for development, if you want to use --reload, run under hypercorn:
    `python -m hypercorn --reload materialist.steamcmd:app --config python:materialist.steamcmd.config`

for release, run this module/script directly:
    `python -m materialist.steamcmd`

depends on:
  hypercorn[trio]
  starlette
"""

from argparse import ArgumentParser
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
import json
import hashlib
import shutil
from pprint import pformat
from pathlib import Path
from subprocess import CompletedProcess, CalledProcessError, PIPE, STDOUT

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import Response, JSONResponse as JsonResponse

import trio

import materialist.core
from materialist import logging
from materialist.core import (
    exception_handlers,
    HTTP_BAD_REQUEST,
    HTTP_TOO_MANY_REQUESTS,
    HTTP_SERVER_ERROR,
    MILLIS,
)

log = logging.getLogger("materialist.steamcmd")

# https://www.gnu.org/software/tar/manual/html_node/Reproducibility.html
# this isn't actually reproducible because mtime but i stopped caring
TAR_REPRODUCIBLE = [
    "--sort=name",
    "--format=posix",
    "--numeric-owner",
    "--pax-option='delete=atime,delete=ctime'",
    "--owner=0",
    "--group=0",
]

# RUN_DIR = Path("/var/run/materialist-steamcmd")
RUN_DIR = Path("/tmp/materialist-steamcmd")
SCRATCH_DIR = RUN_DIR / "scratch"


class SteamApiOutputBuf(object):
    READYBYTES = b"\nSteam>\x1b[0m"

    def __init__(self, stdout):
        self.buf = deque(maxlen=64)
        self.stdout = stdout

    # def is_ready_for_command(self, buf=b""):
    #     self.buf.extend(buf)
    #     return bytes(self.buf).endswith(self.READYBYTES)

    async def read_until_ready(self):
        """This will block if you call it while ready!"""
        while b := await self.stdout.receive_some():

            log.butt(steamcmd=b)
            self.buf.extend(b)
            if bytes(self.buf).endswith(self.READYBYTES):
                return True

        return False


async def run_one_steamcmd(req_r, *, task_status=trio.TASK_STATUS_IGNORED):
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

        # # pyright ...
        # assert isinstance(steamcmd, trio.Process)
        # assert steamcmd.stdin
        # assert steamcmd.stdout

        task_status.started(steamcmd)

        buf = SteamApiOutputBuf(steamcmd.stdout)

        try:
            if not await buf.read_until_ready():
                log.warn("steamcmd EOF before startup")
            else:
                log.info("steamcmd ready")

            while True:

                # if this is closed, our service is shutting down
                try:
                    msg: DownloadRequest = await req_r.receive()
                except (trio.EndOfChannel, trio.ClosedResourceError):
                    log.warn("steamcmd req_r channel closed")
                    break

                apppath = SCRATCH_DIR / f"app_{msg.appid}"
                itempath = apppath / f"item_{msg.itemid}"

                try:

                    await steamcmd.stdin.send_all(
                        b"download_item %s %s\n"
                        % (msg.appid.encode(), msg.itemid.encode())
                    )

                    if not await buf.read_until_ready():
                        log.warn("steamcmd EOF during download_item")
                        break

                    try:
                        result: CompletedProcess = await tar_path(itempath)
                    except CalledProcessError as err:
                        log.warn(
                            "steamcmd tar failed",
                            code=err.returncode,
                            stderr=err.stderr,
                        )
                        reply = ":<"  # FIXME use a real error thingy
                    else:
                        reply = result.stdout

                    try:
                        msg.reply.send_nowait(reply)
                    except trio.WouldBlock:
                        continue

                finally:

                    try:
                        # clean up downloaded whatevers as much as possible
                        # the file is downloaded to itempath but there's an
                        # extra `app_602960/state_602960_602960.patch` file
                        # that is weird and messes with things
                        await trio.to_thread.run_sync(shutil.rmtree, apppath)
                    except OSError as err:
                        log.warn("failed to clean up itempath %s: %s", apppath, err)

        finally:
            with trio.move_on_after(250 * MILLIS, shield=True):
                try:
                    await steamcmd.stdin.send_all(b"\nquit\n")
                except (trio.BrokenResourceError, trio.ClosedResourceError):
                    pass

            with trio.move_on_after(3, shield=True):
                r = await steamcmd.wait()
                log.info("steamcmd exit » %s", r)

            log.info("%s", bytes(buf.buf))

        log.info("steamcmd done!")

    log.info("steamcmd nursery closed!")


async def tar_path(path):
    return await trio.run_process(
        ["tar", "-c", "--zstd", *TAR_REPRODUCIBLE, "-C", path, "."],
        capture_stdout=True,
        capture_stderr=True,
    )


@dataclass
class DownloadRequest(object):
    appid: str
    itemid: str
    reply: trio.MemorySendChannel


async def download(request):
    try:
        body = await request.json()
    except json.decoder.JSONDecodeError:
        return HTTP_BAD_REQUEST

    # log.info("req headers » %s", pformat(request.headers))
    log.info("req body » %s", pformat(body))

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

    with log.clocked("hash_tar"):
        try:
            etag = await hash_tar(woot)
        except* CalledProcessError as errs:
            for err in errs.exceptions:
                assert isinstance(err, CalledProcessError)
                log.crit(
                    "hash_tar failed",
                    code=err.returncode,
                    stderr=err.stderr,
                )
            raise

    return Response(woot, headers={"etag": etag})


async def hash_tar(tar_data):
    h = hashlib.blake2s()
    stderr = []

    async with trio.open_nursery() as nursery:
        tar = await nursery.start(
            partial(
                trio.run_process,
                ["tar", "-t", "--zstd"],
                stdin=tar_data,
                stdout=PIPE,
                capture_stderr=True,
            )
        )

        while b := await tar.stdout.receive_some():
            h.update(b)

    return h.hexdigest()


@asynccontextmanager
async def lifespan(app):
    RUN_DIR.mkdir(mode=0o770, exist_ok=True)
    SCRATCH_DIR.mkdir(exist_ok=True)

    async with trio.open_nursery() as nursery:

        req_s, req_r = trio.open_memory_channel(0)
        with req_s, req_r:
            app.state.req_s = req_s
            app.state.req_r = req_r

            log.butt("starting steamcmd in background")
            steamcmds = []
            if app.state.args.steamcmds > 0:
                steamcmds.append(await nursery.start(run_one_steamcmd, req_r))
            log.butt("lifespan up")
            try:
                yield
            finally:
                for r in steamcmds:
                    try:
                        # # pyright ...
                        # assert isinstance(r, trio.Process)
                        # assert r.stdin
                        await r.stdin.send_all(b"\nquit\n")
                    except (trio.BrokenResourceError, trio.ClosedResourceError):
                        pass
                log.butt("lifespan down")


# this isn't used, if we have multiple steamcmds we might use this to handle failures idk
shutdown = trio.Event()

routes = [Route("/download/", download, methods=["POST"])]

app = Starlette(routes=routes, lifespan=lifespan, exception_handlers=exception_handlers)


class config(materialist.core.config):
    bind = ["127.0.0.1:8888"]


def main():
    logging.basicConfig(level=logging.DEBUG)

    from materialist.core import hypercorn_config
    from hypercorn.trio import serve

    parser = ArgumentParser()
    parser.add_argument("-n", "--steamcmds", default=1, type=int)
    args = parser.parse_args()

    log.debug(args)

    app.state.args = args

    trio.run(
        partial(serve, app, hypercorn_config(config), shutdown_trigger=shutdown.wait)
    )


if __name__ == "__main__":
    main()
