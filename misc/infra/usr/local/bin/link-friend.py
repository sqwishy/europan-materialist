#!/usr/bin/env python3
r"""
TODO
"""

from sys import stderr
from struct import Struct
from pwd import getpwuid
from os import unlink, chmod, environ, readlink
from socket import socket, AF_UNIX, SO_PEERCRED, SO_PASSCRED
from subprocess import run as subprocess_run, CalledProcessError


# struct ucred { s32 pid; u32 uid; u32 gid };
UCRED = Struct('iI') # gid not used


if stderr.isatty() and "NO_COLOR" not in environ:
    ANSI_RESET = "\x1b[0m"
    ANSI_RED = "\x1b[38;5;9m"
    ANSI_YELLOW = "\x1b[38;5;11m"
    ANSI_MAGENTA = "\x1b[38;5;13m"
    ANSI_TEAL = "\x1b[38;5;14m"
else:
    ANSI_RESET = ANSI_RED = ANSI_YELLOW = ANSI_MAGENTA = ANSI_TEAL = ""


if "JOURNAL_STREAM" in environ:
    # https://www.freedesktop.org/software/systemd/man/sd-daemon.html
    PREFIX_OOPS = "<3>"  # <3> looks red
    PREFIX_WARN = "<4>"  # <4> looks kinda yellow, <5> is bold
    PREFIX_INFO = "<6>"  # <6> normal
    PREFIX_SUBP = "<7>"  # <7> grey like commented out
else:
    PREFIX_OOPS = PREFIX_WARN = PREFIX_INFO = PREFIX_SUBP = ""


def _log(level, *args, **extra):
    extra = "".join(f"\n     {k} ➭ {v}" for k, v in extra.items() if v != "")
    print(level, *args, extra, file=stderr)


def log_info(*args, **extra):
    _log(f"{PREFIX_INFO}{ANSI_TEAL}info{ANSI_RESET}", *args, **extra)

def log_warn(*args, **extra):
    _log(f"{PREFIX_WARN}{ANSI_YELLOW}warn{ANSI_RESET}", *args, **extra)

def log_subp(*args, **extra):
    _log(f"{PREFIX_SUBP}{ANSI_MAGENTA}subp{ANSI_RESET}", *args, **extra)


def run(*argv):
    completed = subprocess_run(argv, capture_output=True, encoding="utf8")
    completed.check_returncode()  # may raise CalledProcessError
    log_subp(*argv, stderr=completed.stderr)
    return completed


def unix_listen(path):
    try:
        unlink(path)
    except FileNotFoundError:
        pass

    sock = socket(family=AF_UNIX)
    sock.bind(path)

    chmod(path, 0o777)

    sock.listen()

    return sock


def handle_peer(peer, *, our_netns): 
    pid, uid = UCRED.unpack(peer.getsockopt(AF_UNIX, SO_PEERCRED, UCRED.size))
    if uid != 50002:
        log_warn("ignoring uid %s, not 50002" % uid)
        return

    # getpwuid(uid).pw_name materialist

    nspath = f"/proc/{pid}/ns/net"

    if readlink(nspath) == our_netns:
        log_warn("ignoring pid %s, connection in own namespace" % pid)
        return

    try:
        run("/usr/sbin/ip", "link",
            "add", "spl-peer",
            "group", "66",
            "type", "veth",
            "peer", "spl-host",
            "netns", nspath)
        run("/usr/bin/nsenter", f"--net={nspath}",
            "/usr/sbin/ip", "a", "add", "10.23.23.2/28", "dev", "spl-host")
        run("/usr/bin/nsenter", f"--net={nspath}",
            "/usr/sbin/ip", "link", "set", "spl-host", "up")
        run("/usr/bin/nsenter", f"--net={nspath}",
            "/usr/sbin/ip", "route", "add", "default", "via", "10.23.23.1")
    except CalledProcessError as err:
        log_warn(*err.cmd, code=err.returncode, stdout=err.stdout, stderr=err.stderr)


def main():
    from argparse import (
        ArgumentParser,
        RawDescriptionHelpFormatter,
        ArgumentDefaultsHelpFormatter,
    )

    class formatter_class(RawDescriptionHelpFormatter, ArgumentDefaultsHelpFormatter):
        pass

    parser = ArgumentParser(formatter_class=formatter_class, epilog=__doc__)
    # fmt: off
    parser.add_argument("--socket", default="/run/link-friend.unix")
    # fmt: on
    args = parser.parse_args()

    our_netns = readlink("/proc/self/ns/net")

    sock = unix_listen(args.socket)
    log_info("listening on", args.socket)

    while True:
        try:
            peer, _ = sock.accept()
        except KeyboardInterrupt:
            raise SystemExit(0)
        try:
            handle_peer(peer, our_netns=our_netns)
        finally:
            peer = None



if __name__ == "__main__":
    main()
