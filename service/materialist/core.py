from starlette.responses import Response

import materialist.logging


class config:
    """ This is for running hypercorn in the foreground in development mode.

    These correspond to attributes on hypercorn.config.Config, but we can't
    actually use an instance of that here for no apparent reason.
    """

    worker_class = "trio"
    logger_class = materialist.logging.HypercornLogger


def hypercorn_config(config, dest=None):
    """ Since the config class above can't be an instance of a
    hypercorn.config.Config, this just copies attributes onto an instance of
    hypercorn.config.Config and returns it
    """
    from hypercorn.config import Config

    dest = Config() if dest is None else dest

    for k in dir(config):
        if not k.startswith("_"):
            setattr(dest, k, getattr(config, k))

    return dest


exception_handlers = {
    404: lambda _req, _exc: HTTP_NOT_FOUND,
    500: lambda _req, _exc: HTTP_SERVER_ERROR,
}

HTTP_BAD_REQUEST = Response("bad request", status_code=400)
HTTP_NOT_FOUND = Response("not found", status_code=404)
HTTP_TOO_MANY_REQUESTS = Response(
    "this computer too busy (x . x) ~~zzZ", status_code=429
)
HTTP_SERVER_ERROR = Response(
    "something went fucky wucky on the computer doing the website and the thing didn't work, sorry",
    status_code=500,
)

BAROTRAUMA_APPID = '602960'

MILLIS = 1.0 / 1000.0
