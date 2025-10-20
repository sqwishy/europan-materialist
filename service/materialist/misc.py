from functools import partial
from operator import setitem, itemgetter


def itemsetter(dest, attr):
    return partial(setitem, dest, attr)


def ritemgetter(*items):
    def inner(v):
        for item in items:
            v = v[item]
        return v

    return inner


def linear_lookup(d):
    """Does not copy the argument"""
    if isinstance(d, dict):
        return lambda match: next((v for k, v in d.items() if k == match), None)
    else:  # tuple or list of tuples hopefully
        return lambda match: next((v for k, v in d if k == match), None)


def sliced_at(s, index):
    return s[:index], s[index:]
