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
          -v /tmp/materialist-steamcmd:/tmp/materialist-steamcmd \
          -v /run/user/1000/podman:/run/podman \
          --rm -it materialist-steamcmd
"""

from argparse import ArgumentParser, ArgumentDefaultsHelpFormatter
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial
import hashlib
import shutil
import base64
import tarfile
import io
import os

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

FORMATS = ("tar", "tar.zstd")


class SteamcmdOutputBuf(object):
    READYBYTES = b"\nSteam>\x1b[0m"

    def __init__(self, stdout):
        self.buf = deque(maxlen=64)
        self.stdout = stdout

    def has_failure(self):
        return b"(Failure)" in bytes(self.buf)

    async def read_until_ready(self):
        """This will block if you call it while it's already ready!

        Returns False if we get EOF before reading a ready message.
        """
        while b := await self.stdout.receive_some():

            log.butt(steamcmd=b)
            self.buf.extend(b)
            if bytes(self.buf).endswith(self.READYBYTES):
                return True

        return False


def test_buffer_read_failure():
    import trio.testing

    s, r = trio.testing.memory_stream_pair()
    buf = SteamcmdOutputBuf(r)

    success = b'\n\x1b[0mSuccess. Downloaded item 3478666406 to "/root/Steam/steamapps/workshop/content/602960/3478666406" (165113 bytes) \x1b[0m\x1b[1m\nSteam>\x1b[0m'
    failure = b"\n\x1b[0mERROR! Download item 3045796581 failed (Failure).\x1b[0m\x1b[1m\nSteam>\x1b[0m"

    @trio.run
    async def wow():
        await s.send_all(success)
        with trio.fail_after(0.01):
            assert await buf.read_until_ready()
            assert not buf.has_failure()

        await s.send_all(failure)
        with trio.fail_after(0.01):
            assert await buf.read_until_ready()
            assert buf.has_failure()


async def run_one_steamcmd(
    req_r, workdir, image, retries, *, task_status=trio.TASK_STATUS_IGNORED
):
    args = [
        "podman-remote",
        "run",
        "--rm",
        "-i",
        # This is shared with the most so we can remove the downloaded files at
        # runtime. But it does not need to be backed by a disk.
        "-v",
        f"{workdir}:/root/Steam/steamapps/workshop/content",
        image,
        # "+force_install_dir", "/tmp/bind",
        "+login anonymous",
    ]

    async with trio.open_nursery() as nursery:
        steamcmd = await nursery.start(
            partial(trio.run_process, args, stdin=PIPE, stdout=PIPE, stderr=STDOUT)
        )

        task_status.started(steamcmd)

        buf = SteamcmdOutputBuf(steamcmd.stdout)

        try:
            if not await buf.read_until_ready():
                log.warn("steamcmd EOF before startup")
            else:
                log.info("steamcmd ready")

            await run_steamcmd_forever(req_r, steamcmd, buf, workdir, retries)
        finally:
            with trio.move_on_after(250 * MILLIS, shield=True):
                try:
                    await steamcmd.stdin.send_all(b"\nquit\n")
                except (trio.BrokenResourceError, trio.ClosedResourceError):
                    pass

            with trio.move_on_after(3, shield=True):
                r = await steamcmd.wait()
                log.info("steamcmd exit » %s", r)

        log.info("steamcmd done!")

    log.info("steamcmd nursery closed!")


async def run_steamcmd_forever(
    req_r,
    steamcmd,
    buf,
    workdir,
    retries,
):
    while True:
        try:
            msg: DownloadRequest = await req_r.receive()
        except (trio.EndOfChannel, trio.ClosedResourceError):
            # if this is closed, the service is shutting down
            break

        itempath = workdir / msg.appid / msg.itemid

        try:
            reply = None

            if await download_one_steamcmd(
                steamcmd, buf, msg.appid, msg.itemid, retries=retries
            ):
                try:
                    with log.clocked("tar_path"):
                        result: CompletedProcess = await tar_path(
                            itempath, msg.tar_opts
                        )
                except CalledProcessError as err:
                    log.warn(
                        "steamcmd tar failed", code=err.returncode, stderr=err.stderr
                    )
                else:
                    reply = result.stdout

            try:
                msg.reply.send_nowait(reply)
            except trio.WouldBlock:
                pass

        finally:

            try:
                await trio.to_thread.run_sync(rmdir, itempath)
            except OSError as err:
                log.warn("failed rmtree", itempath=itempath, err=err)
            else:
                log.butt("removed", itempath=itempath)


async def download_one_steamcmd(
    steamcmd, buf, appid: str, itemid: str, *, retries: int
):
    for tries_left in reversed(range(retries)):
        await steamcmd.stdin.send_all(
            b"workshop_download_item %s %s\n" % (appid.encode(), itemid.encode())
        )

        if not await buf.read_until_ready():
            log.warn("steamcmd EOF during download_item")
            return

        if buf.has_failure():
            if tries_left:
                log.warn("steamcmd download failed, retrying ... %i attempts left")
            else:
                log.warn("steamcmd download failed, giving up")
        else:
            return True


def rmdir(path):
    fd = os.open(path, os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    shutil.rmtree(path)


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
    tar_opts = [s for e in exclude for s in ("--exclude", e)]

    wait = prefer_wait(request) or 0

    reply_s, reply_r = trio.open_memory_channel(0)

    async with reply_r, reply_s:
        msg = DownloadRequest(
            appid=appid, itemid=itemid, tar_opts=tar_opts, reply=reply_s
        )

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

    return Response(
        tar_data, headers={"etag": f'"{etag}"', "uncompressed-size": str(size)}
    )


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
    image = app.state.args.image
    retries = max(app.state.args.retries, 1)

    app.state.args.workdir.mkdir(mode=0o770, exist_ok=True)

    async with trio.open_nursery() as nursery:

        req_s, req_r = trio.open_memory_channel(0)
        with req_s, req_r:
            app.state.req_s = req_s
            app.state.req_r = req_r

            log.butt("starting steamcmd in background")
            steamcmds = []
            for i in range(app.state.args.steamcmds):
                workdir = app.state.args.workdir / f"{i}"
                workdir.mkdir(mode=0o770, exist_ok=True)
                steamcmds.append(
                    await nursery.start(
                        run_one_steamcmd, req_r, workdir, image, retries
                    )
                )
            log.butt("lifespan up")
            try:
                yield
            finally:
                for r in steamcmds:
                    try:
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

    parser = ArgumentParser(formatter_class=ArgumentDefaultsHelpFormatter)
    # fmt: off
    parser.add_argument("-n", "--steamcmds", default=1, type=int, help="nubmer of steamcmd containers to run")
    parser.add_argument("-l", "--listen", action="append", type=str, help="listen address")
    parser.add_argument("-i", "--image", default="steamcmd/steamcmd:alpine", type=str, help="container image")
    parser.add_argument("-r", "--retries", default=3, type=int, help="max download retry attempts")
    parser.add_argument("-w", "--workdir", default="/tmp/materialist-steamcmd", type=Path, help="temporary file download directory")
    # fmt: on
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
