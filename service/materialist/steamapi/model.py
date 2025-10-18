from dataclasses import dataclass

from materialist.misc import linear_lookup


def isinstance_or_raise(v, inst):
    if not isinstance(v, inst):
        raise ValueError("expected %r, got %r" % (inst, v))
    return v


@dataclass
class WorkshopItemVersion():
    workshopid: str
    title: str
    author: str
    created_at: int
    updated_at: int
    consumer_app_id: int

    @classmethod
    def from_steamapi(cls, values: dict):
        """
        >>> WorkshopItemVersion.from_steamapi({
        ...     'consumer_app_id': 602960,
        ...     'creator': '1111',
        ...     'creator_app_id': 602960,
        ...     'publishedfileid': '2222',
        ...     'result': 1,
        ...     'subscriptions': 8,
        ...     'tags': [],
        ...     'time_created': 1234561234,
        ...     'title': 'funky-monkey',
        ...     'visibility': 0,
        ... })
        WorkshopItemVersion(workshopid='2222', title='funky-monkey', author='1111', created_at=1234561234, updated_at=1234561234, consumer_app_id=602960)
        """
        f = linear_lookup(values)
        return cls(
            workshopid=isinstance_or_raise(f("publishedfileid"), str),
            title=isinstance_or_raise(f("title"), str),
            author=isinstance_or_raise(f("creator"), str),
            created_at=isinstance_or_raise(f("time_created"), int),
            updated_at=isinstance_or_raise(f("time_updated") or f("time_created"), int),
            consumer_app_id=isinstance_or_raise(f("consumer_app_id"), int),
        )


@dataclass
class PlayerVersion():
    steamid: str
    name: str
    url: str

    @classmethod
    def from_steamapi(cls, values: dict):
        """
        >>> PlayerVersion.from_steamapi({
        ...     'steamid': '1111',
                "communityvisibilitystate": 3,
        ...     "profilestate": 1,
        ...     "personaname": "xxxSpongeBob",
        ...     "commentpermission": 1,
        ...     "profileurl": "/dev/null",
        ...     "avatar": "htts://example.com/avatar",
        ...     "avatarmedium": "htts://example.com/avatarmedium",
        ...     "avatarfull": "htts://example.com/avatarfull",
        ...     "avatarhash": "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        ...     "lastlogoff": 1234,
        ...     "personastate": 0,
        ...     "realname": "Spongebob Squarepants",
        ...     "timecreated": 1234,
        ...     "personastateflags": 0,
        ...     "loccountrycode": "CA",
        ... })
        PlayerVersion(steamid='1111', name='xxxSpongeBob', url='/dev/null')
        """
        f = linear_lookup(values)
        return cls(
            steamid=isinstance_or_raise(f("steamid"), str),
            name=isinstance_or_raise(f("personaname"), str),
            url=isinstance_or_raise(f("profileurl"), str),
        )

