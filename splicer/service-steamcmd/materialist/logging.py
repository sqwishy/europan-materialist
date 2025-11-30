import sys, os
from contextlib import contextmanager
from time import monotonic_ns
# import arrow
from datetime import datetime

from materialist import ansi

BUTT = DEBG = DEBUG = 0
TIME = 1
INFO = 2
WARN = WARNING = 3
CRIT = CRITICAL = ERROR = 4  # ehh ... who cares..

# includes aliases
ALL_LEVEL_NAMES = set(k.lower() for k in globals().keys() if k.isupper())
# indexed by level value
LEVEL_NAMES = ["BUTT", "TIME", "INFO", "WARN", "CRIT"]
LEVELS = [DEBUG, TIME, INFO, WARNING, CRITICAL]

CHOICES = type(
    "choices containing aliases",
    (list,),
    {"__contains__": lambda _, v: v in ALL_LEVEL_NAMES},
)(n.lower() for n in LEVEL_NAMES)

COLORS = [
    ansi.teal,
    ansi.yellow,
    ansi.blue,
    ansi.magenta,
    ansi.red,
]

_CLOCK_STACK = []

FILE = sys.stderr

if not os.isatty(FILE.fileno()) or "NOCOLOR" in os.environ:

    def level_color(_, txt):
        return txt

else:

    def level_color(lvl, txt):
        try:
            color = COLORS[lvl]
        except IndexError:
            return txt
        else:
            return color(txt)


class Logger(object):
    def __init__(self, *, name, parent):
        if name is None:
            self.name = "~root~"
        else:
            self.name = name
        self.parent = parent
        self.lvl_filter = None

        while parent is not None:
            assert self != parent, "recursive logger relationship"
            parent = parent.parent

        self.fmt = "%(ts)s %(level)s [%(name)s]\t%(msg)s%(extra)s"

    def setLevel(self, lvl):
        self.lvl_filter = lvl

    def _includeLvl(self, lvl):
        """drop messages if a lvl_filter is above lvl for us or any of our parents"""
        if self.lvl_filter is not None and lvl < self.lvl_filter:
            return False
        elif self.parent is not None:
            return self.parent._includeLvl(lvl)
        else:
            return True

    def is_filtered(self, lvl):
        return not self._includeLvl(lvl)

    def log(self, lvl, *args, exc_info=None, **extra):
        return self._log(lvl, args, extra, exc_info=exc_info)

    def _log(self, lvl, args, extra, *, exc_info=None):
        if not self._includeLvl(lvl):
            return

        try:
            lvltxt = LEVEL_NAMES[lvl]
        except IndexError:
            lvltxt = str(lvl)

        if args:
            try:
                msg = str(args[0]) % args[1:]
            except (TypeError, IndexError, KeyError, ValueError) as e:
                msg = f"failed interpolation {args}: {repr(e)}"
        else:
            msg = ""

        lvltxt = level_color(lvl, lvltxt)

        if extra:
            # msg += "".join(f"\t» {k} {v}" for k, v in extra.items())
            extra = "".join(f"\n\t{k} ➭ {v}" for k, v in extra.items())
        else:
            extra = ""

        # ts = ansi.dark_grey(arrow.now())
        ts = ansi.dark_grey(datetime.now())

        try:
            msg = self.fmt % {
                "ts": ts,
                "level": lvltxt,
                "msg": msg,
                "name": self.name,
                "extra": extra,
            }
        except (TypeError, IndexError, KeyError, ValueError) as e:
            msg = f"invalid format {repr(e)} {self.fmt} lvl:{lvltxt} msg:{msg}"

        print(msg, file=FILE)

        if exc_info:
            import traceback

            traceback.print_exception(*exc_info, file=FILE)

    def butt(self, *args, **kwargs):
        return self.log(BUTT, *args, **kwargs)

    def debug(self, *args, **kwargs):
        return self.log(DEBUG, *args, **kwargs)

    def time(self, *args, **kwargs):
        return self.log(TIME, *args, **kwargs)

    def info(self, *args, **kwargs):
        return self.log(INFO, *args, **kwargs)

    def warning(self, *args, **kwargs):
        return self.log(WARNING, *args, **kwargs)
    
    warn = warning

    def error(self, *args, **kwargs):
        return self.log(ERROR, *args, **kwargs)

    def critical(self, *args, **kwargs):
        return self.log(CRITICAL, *args, **kwargs)

    crit = critical

    def exception(self, *args, **kwargs):
        return self.log(CRITICAL, *args, exc_info=sys.exc_info(), **kwargs)

    @contextmanager
    def clocked(self, *args, **kwargs):
        sp = len(_CLOCK_STACK)
        start = monotonic_ns()
        try:
            yield
        finally:
            yield_ns = monotonic_ns() - start
            child_ns = sum(_CLOCK_STACK[sp:])
            self_ns = yield_ns - child_ns  # subtract any clocked children

            _CLOCK_STACK[sp:] = (yield_ns,)

            self.time(*args, all=ns_as_ms(yield_ns), slf=ns_as_ms(self_ns), **kwargs)


INSTANCES = {}  # type: ignore


def getLogger(name=None):
    logger = INSTANCES.get(name)
    if logger is None:
        logger = INSTANCES[name] = _makeLogger(name)
    return logger


def _makeLogger(name):
    if name is None:
        parent = None
    else:
        leading, _, _ = name.rpartition(".")
        if leading:
            parent = getLogger(name=leading)
        else:
            parent = getLogger()  # root/parentless logger
    return Logger(name=name, parent=parent)


ROOT = getLogger()


def basicConfig(level=DEBUG):
    ROOT.lvl_filter = level


def clocked(*args, **kwargs):
    f = sys._getframe(2)  # 2 = 1 + contextlib's contextmanager

    logger = f.f_globals.get("logger")
    if logger is None:
        logger = getLogger(f.f_globals["__name__"])

    return logger.clocked(*args, **kwargs)


class ns_as_ms(int):
    NS_IN_MS = 1_000_000

    def __str__(self):
        return f"{self / self.NS_IN_MS:.02f}ms"


class HypercornLogger(object):
    def __init__(self, config):
        self._logger = getLogger("hypercorn")

    async def access(self, request, response, request_time: float) -> None:
        client = request.get("client")
        if client is None:
            remote_addr = None
        elif len(client) == 2:
            remote_addr = f"{client[0]}:{client[1]}"
        elif len(client) == 1:
            remote_addr = client[0]
        else:
            remote_addr = f"?{client}?"

        query_string = request["query_string"].decode()
        path_with_qs = request["path"] + ("?" + query_string if query_string else "")
        protocol = request.get("http_version", "ws")
        response_status = '‥' if response is None else response.get("status")
        method = request.get("method", "?")

        try:
            msg = f"«{remote_addr}» {protocol} {method} {path_with_qs} {response_status} ~ {request_time * 1000:.2f}ms"
        except Exception:
            self._logger.exception(
                "failed to interpolate access log", request=request, response=response
            )
            raise

        self._logger.info(msg)

    async def critical(self, msg: str, *args, **kwargs) -> None:
        self._logger.critical(msg, *args, **kwargs)

    async def error(self, msg: str, *args, **kwargs) -> None:
        self._logger.error(msg, *args, **kwargs)

    async def warning(self, msg: str, *args, **kwargs) -> None:
        self._logger.warning(msg, *args, **kwargs)

    async def info(self, msg: str, *args, **kwargs) -> None:
        self._logger.info(msg, *args, **kwargs)

    async def debug(self, msg: str, *args, **kwargs) -> None:
        self._logger.butt(msg, *args, **kwargs)

    async def exception(self, msg: str, *args, **kwargs) -> None:
        self._logger.exception(msg, *args, **kwargs)

    async def log(self, level: int, msg: str, *args, **kwargs) -> None:
        self._logger.log(level, msg, *args, **kwargs)


if __name__ == "__main__":  # lmao nice unit testing buddy
    from itertools import cycle
    from textwrap import dedent

    ROOT.info("test kwargs", dank="memes", blazeit=420)

    lines = (
        dedent(
            """
        Have you ever wondered why clouds behave in such familiar ways when
        each specimen is so unique?  Or why the energy exchange market is so
        unpredictable?  In the coming age we must develop and apply nonlinear
        mathematical models to real world phenomena. We shall seek, and find,
        the hidden fractal keys which can unravel the chaos around us.

        -- Academician Prokhor Zakharov, University Commencement
        """
        )
        .strip()
        .splitlines()
    )
    for line, lvl in zip(lines, cycle(LEVELS)):
        ROOT.log(lvl, line)
