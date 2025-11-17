""" ฅ^•ﻌ•^ฅ 

for development, if you want to use --reload, run under hypercorn:
    `python -m hypercorn --reload materialist.steamcmd:app --config python:materialist.steamcmd.config`

for release, run this module/script directly:
    `python -m materialist.steamcmd`

depends on:
  hypercorn[trio]
  starlette

use in podman:
  podman build -f Containerfile --tag materialist-steamcmd
  podman run \
          --image-volume=tmpfs \
          -v /run/user/1000/podman:/run/podman \
          --rm -it materialist-steamcmd
"""

from argparse import ArgumentParser
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
import hashlib
import shutil
import base64
import tarfile
import io

# import compression.zstd
from pathlib import Path
from subprocess import CompletedProcess, CalledProcessError, PIPE, STDOUT

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.responses import Response
from starlette.convertors import Convertor, register_url_convertor

import trio

import materialist.core
from materialist import logging
from materialist.core import (
    exception_handlers,
    HTTP_NOT_FOUND,
    HTTP_BAD_REQUEST,
    HTTP_TOO_MANY_REQUESTS,
    HTTP_SERVER_ERROR,
    MILLIS,
)

log = logging.getLogger("materialist.steamcmd")

# https://www.gnu.org/software/tar/manual/html_node/Reproducibility.html
TAR_REPRODUCIBLE = [
    "--sort=name",
    "--format=posix",
    "--numeric-owner",
    "--pax-option=delete=atime,delete=ctime",
    "--owner=0",
    "--group=0",
    "--mtime=UTC 2000-1-1",
]

# RUN_DIR = Path("/var/run/materialist-steamcmd")
RUN_DIR = Path("/tmp/materialist-steamcmd")
SCRATCH_DIR = RUN_DIR / "scratch"

FORMATS = ("tar", "tar.zstd")


class SteamApiOutputBuf(object):
    READYBYTES = b"\nSteam>\x1b[0m"

    def __init__(self, stdout):
        self.buf = deque(maxlen=64)
        self.stdout = stdout

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
        "podman-remote",
        "run",
        "--rm",
        "-i",
        # This is shared with the most so we can remove the downloaded files at
        # runtime. But it does not need to be backed by a disk.
        #
        # FIXME Each steamcmd should have its own SCRATCH_DIR as to not collide
        # if they each download the same thing at the same time for some stupid
        # reason.
        "-v",
        # f"{SCRATCH_DIR}:/root/.local/share/Steam/steamcmd/linux32/steamapps/content",
        f"{SCRATCH_DIR}:/root/Steam/steamapps/workshop/content",
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

                itempath = SCRATCH_DIR / msg.appid / msg.itemid

                try:

                    await steamcmd.stdin.send_all(
                        b"workshop_download_item %s %s\n"
                        % (msg.appid.encode(), msg.itemid.encode())
                    )

                    if not await buf.read_until_ready():
                        log.warn("steamcmd EOF during download_item")
                        break

                    try:
                        with log.clocked("tar_path"):
                            result: CompletedProcess = await tar_path(itempath, msg.tar_opts)
                    except CalledProcessError as err:
                        log.warn(
                            "steamcmd tar failed",
                            code=err.returncode,
                            stderr=err.stderr,
                        )
                        reply = b":<"  # FIXME use a real error thingy
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
                        await trio.to_thread.run_sync(shutil.rmtree, itempath)
                    except OSError as err:
                        log.warn("failed to clean up itempath %s: %s", itempath, err)

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


async def tar_path(path, tar_opts=()):
    return await trio.run_process(
        ["tar", *TAR_REPRODUCIBLE, *tar_opts, "-C", path, "-c", "."],
        capture_stdout=True,
        capture_stderr=True,
    )


@dataclass
class DownloadRequest(object):
    appid: str
    itemid: str
    tar_opts: list[str]
    reply: trio.MemorySendChannel


def prefer_wait(request):
    if prefer := request.headers.get("prefer"):
        if prefer.startswith("wait="):
            try:
                return int(prefer.removeprefix("wait=").strip())
            except ValueError:
                log.exception("parse wait=", prefer=prefer)


async def download(request):

    log.butt(request.path_params)

    if not (appid := request.path_params.get("app")):
        return HTTP_NOT_FOUND

    if not (itemid := request.path_params.get("item")):
        return HTTP_NOT_FOUND

    if (format := request.path_params.get("format")) not in FORMATS:
        return HTTP_NOT_FOUND

    exclude = request.query_params.getlist("exclude")
    tar_opts = [s for e in exclude for s in ('--exclude', e)]

    wait = prefer_wait(request) or 0

    reply_s, reply_r = trio.open_memory_channel(0)

    async with reply_r, reply_s:
        msg = DownloadRequest(appid=appid, itemid=itemid, tar_opts=tar_opts, reply=reply_s)

        if not wait:
            try:
                request.app.state.req_s.send_nowait(msg)
            except trio.WouldBlock:
                return HTTP_TOO_MANY_REQUESTS

        else:
            with trio.move_on_after(wait) as cancel_scope:
                # FIXME we should race between send() and some awaitable
                # that resolves when the requester disconnects, but
                # request.is_disconnected() doesn't have an option to
                # wait for a disconnect. so we'd probably have to do that
                # ourselves with its non-public API...
                await request.app.state.req_s.send(msg)

            if cancel_scope.cancelled_caught:
                return HTTP_TOO_MANY_REQUESTS

        tar_data: bytes = await reply_r.receive()

    with log.clocked("tar_hash_and_size"):
        etag, size = await trio.to_thread.run_sync(tar_hash_and_size, tar_data)

    if format == "tar.zstd":
        with log.clocked("zstd_tar"):
            tar_data = await zstd_tar(tar_data)

    return Response(tar_data, headers={"etag": f'"{etag}"', "uncompressed-size": str(size)})


def tar_hash_and_size(tar_data: bytes) -> tuple[str, int]:
    """hash of file names and contents & sum size of contents

    hash is blake2s size 20 & prefixed with size is i32 big endian. then encoded
    as urlsafe base64 returned as string.

    raises ValueError if the tar's (reported) size is > ~4GB
    """
    t = tarfile.open(fileobj=io.BytesIO(tar_data))

    h = hashlib.blake2s(digest_size=20)
    s = 0

    while tarinfo := t.next():
        s += tarinfo.size
        h.update(tarinfo.name.encode())
        if (reader := t.extractfile(tarinfo)) is not None:
            while buf := reader.read():
                h.update(buf)

    hash: str = base64.urlsafe_b64encode(s.to_bytes(4) + h.digest()).decode()
    return hash, s


async def zstd_tar(tar_data: bytes) -> bytes:
    """todo use the compression package in 3.14"""
    completed = await trio.run_process(
        ["zstd"], stdin=tar_data, capture_stdout=True, capture_stderr=True
    )
    if completed.stderr:
        log.crit("zstd_tar", exit=completed.returncode, stderr=completed.stderr)
    completed.check_returncode()  # raises if returncode is nonzero
    return completed.stdout


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


class AlnumConvertor(Convertor):
    regex = "[a-zA-Z0-9]+"
    convert = to_string = lambda v: v


register_url_convertor("alnum", AlnumConvertor)

routes = [
    Route("/download/{app}/{item:alnum}.{format}", download, methods=["post"]),
]

app = Starlette(routes=routes, lifespan=lifespan, exception_handlers=exception_handlers)


class config(materialist.core.config):
    bind = ["127.0.0.1:8888"]


def main():
    logging.basicConfig(level=logging.DEBUG)

    from materialist.core import hypercorn_config
    from hypercorn.trio import serve

    parser = ArgumentParser()
    parser.add_argument("-n", "--steamcmds", default=1, type=int)
    parser.add_argument("-l", "--listen", action="append", type=str)
    args = parser.parse_args()

    log.debug(args)

    app.state.args = args

    if args.listen:
        config.bind = args.listen

    trio.run(
        partial(serve, app, hypercorn_config(config), shutdown_trigger=shutdown.wait)
    )


if __name__ == "__main__":
    main()
