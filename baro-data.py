import json
import os
import re
import sys
from base64 import b64encode
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import copy
from dataclasses import dataclass, is_dataclass, fields, asdict
from graphlib import TopologicalSorter
from io import BytesIO, StringIO
from itertools import count, chain
from lxml import etree
from operator import ior
from pathlib import Path
from functools import partial, reduce
from typing import (
    Union,
    NewType,
    TypeAlias,
    Callable,
    Iterable,
    Iterator,
    overload,
    TypeVar,
    Literal,
    TYPE_CHECKING,
)
from time import monotonic_ns

if TYPE_CHECKING:
    import PIL


_CHECK_SPRITE_DUPE = False

_CHECK_L10N_MISSING = False

PACKAGE_ORIGIN_KEY = "__package-origin"

TYPES_TS = """\
export type Identifier = string;
export type Money = "$";

export type Part = {
  what: Identifier | Money
  amount: number
  condition?: [number | null, number | null]
}

export type WeightedRandomWithReplacement = {
  weighted_random_with_replacement: Part[];
  amount: number;
}

export type Process = {
  // id: string,
  uses: (Part | WeightedRandomWithReplacement)[],
  skills: Record<Identifier, number>,
  stations: Identifier[],
  time: number
  needs_recipe?: boolean
  description?: string
}

export type Package = {
  name: string,
  version: string | null,
  steamworkshopid: string | null,
}

export type Dictionary = Record<string, string>

export type Bundle = {
  name: string,
  load_order: Package[],
  package_entities: Record<string, Identifier[]>,
  tags_by_identifier: Record<Identifier, Identifier[]>,
  processes: Process[],
  i18n: Record<string, Dictionary>,
}

// export type LoadableDictionary = {
//     url: string,
//     localized_name?: string,
// }

export type LoadableBundle = {
  name: string,
  load_order: Package[],
  url: string, // url to Bundle
  sprites: string, // url to CSS sprite sheet
  // dictionaries: Record<string, LoadableDictionary>
}
"""


class ansi:
    """256-color mode https://en.wikipedia.org/wiki/ANSI_escape_code#8-bit"""

    class Color(object):

        # this stupid fucking text editor's indentation breaks from the
        # unmatched left bracket in strings ...

        reset = "\x1b[0m"  # ]

        def __init__(self, n):
            self.fg = f"\x1b[38;5;{n}m"  # ]

        if sys.stderr.isatty() and "NOCOLOR" not in os.environ:

            def __call__(self, str):
                return f"{self.fg}{str}{self.reset}"

        else:

            def __call__(self, str):
                return str

    blue = Color(12)
    magenta = Color(13)


_LAST_TIME = None


def logtime(message):
    global _LAST_TIME

    if _LAST_TIME is None:
        d = 0.0
    else:
        d = (monotonic_ns() - _LAST_TIME) / 1e6

    prefix = ansi.blue(f"{d: 8.3f}ms")
    print(f"{prefix} » {message}", file=sys.stderr)

    _LAST_TIME = monotonic_ns()


class Error(Exception):
    def __init__(self, **kwargs):
        super().__init__(kwargs)

    def as_warning(self) -> Warning:
        raise NotImplementedError


class MissingAttribute(Error):
    def as_warning(self) -> Warning:
        (args,) = self.args
        return warn_missing_attribute(**args)


class BadValue(Error):
    def as_warning(self) -> Warning:
        (args,) = self.args
        return warn_bad_value(**args)


class Warning(object):
    def __init__(self, message="", **kwargs):
        self.message = message
        self.kwargs = kwargs

    def with_path(self, path):
        self.kwargs["path"] = path
        return self


def warn_missing_attribute(*, element: etree._Element, attribute):
    return Warning(
        "required attribute missing from element", attribute=attribute, element=element
    )


def warn_unexpected_element(*, unexpected):
    return Warning("unexpected element", unexpected=unexpected)


def warn_bad_value(*, error, attribute, element):
    return Warning(
        "bad attribute value", error=error, attribute=attribute, element=element
    )


def log_warnings(it, *, path=None):
    for item in it:
        if isinstance(item, Warning):
            if path:
                item = item.with_path(path)
            log_warning(item.message, **item.kwargs)
        else:
            yield item


def log_warning(message, **kwargs):
    print(ansi.magenta(message), file=sys.stderr)

    for key, value in kwargs.items():
        prefix = f"\t» {ansi.blue(key)} "
        value = format_log_value(value, path=kwargs.get("path"))

        if "\n" in value:
            print(f"{prefix}...\n{value}", file=sys.stderr)
        else:
            print(f"{prefix}{value}", file=sys.stderr)


def format_log_value(v, path=None):
    if isinstance(v, etree._Element):
        # etree.tostringlist does nothing interesting?
        lines = etree.tostring(v).decode().strip().splitlines(keepends=True)

        if not lines:
            return ""

        if v.sourceline is None or path is None:
            return "".join(lines)

        [head, *tail] = lines
        lines = [head] + _dedent_strings(tail)
        width = len(str(v.sourceline + len(lines) - 1))

        return "".join(
            f"{path}:{no:{width}} {line}"
            for no, line in zip(count(v.sourceline), lines)
        )

    elif is_dataclass(v):
        return repr(v)

    elif isinstance(v, Exception):
        return repr(v)

    else:
        return str(v)


def _dedent_strings(strings):
    if not strings:
        return []
    indent = min(re.match(" *", s).end() for s in strings)
    return [s[indent:] for s in strings]


Identifier = NewType("Identifier", str)

Money: TypeAlias = Literal["$"]

MONEY: Money = "$"

RequiredSkill: TypeAlias = dict[Identifier, float]

IDENTIFIER_PATTERN = re.compile(r"[a-z0-9\._]+", flags=re.IGNORECASE)


def make_identifier(value: str) -> Identifier:
    # the game seems to use a lot of case insensitive stuff in Identifier.cs so
    # lowercase these to normalize values
    value = value.strip().lower()

    if IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ValueError(value)

    return Identifier(value)


def split_identifier_list(value: str) -> list[Identifier]:
    return [make_identifier(s) for s in value.split(",") if s]


def split_ltwh(value: str) -> tuple[int, int, int, int]:
    (a, s, d, f) = [int(v.strip()) for v in value.split(",", maxsplit=4)]
    return (a, s, d, f)


def split_int_pair(value: str) -> tuple[int, int]:
    (a, s) = [int(v.strip()) for v in value.split(",", maxsplit=2)]
    return (a, s)


def xmlbool(value: str) -> bool:
    if value.lower() == "true":
        return True
    elif value.lower() == "false":
        return False
    else:
        raise ValueError(value)


def serialize_dataclass(value):
    if is_dataclass(value):
        return dataclass_to_dict_without_defaults(value)
    else:
        raise TypeError(value)


def dataclass_to_dict_without_defaults(value):
    return {
        field.name: getattr(value, field.name)
        for field in fields(value)
        if field.default != getattr(value, field.name)
    }


@dataclass
class Part(object):
    """RequiredItem or any item output of Fabricate or money"""

    what: Union[Identifier, Money]
    # positive if fabrication produces this item, negative if it consumes it
    amount: int
    # condition (like health or quality) required to be consumed or to be
    # yielded? (CopyCondition and OutCondition{Min,Max} is used to specify
    # condition of outputs/yielded items)
    condition: tuple[float | None, float | None] = (None, None)

    @property
    def is_created(self):
        return self.amount > 0

    @property
    def is_consumed(self):
        return self.amount < 0

    def can_combine(self, other: "Part") -> bool:
        """combine amounts for the same part in the same direction"""
        return (
            self.what == other.what
            and self.condition == other.condition
            and sign(self.amount) == sign(other.amount)
        )

    def combine_in_place(self, other: "Part"):
        self.amount += other.amount


@dataclass
class RandomChoices(object):
    """barotrauma seems to do weighted random with replacement ..."""

    weighted_random_with_replacement: list[Part]
    amount: int


@dataclass
class Process(object):
    """Fabricate / Deconstruct / Price"""

    id: str
    uses: list[Part | RandomChoices]
    stations: list[Identifier]
    skills: dict[Identifier, float]
    time: float = 0.0
    # see fabricatorrequiresrecipe in localization strings
    needs_recipe: bool = False
    # see displayname.{description} in localization strings?
    description: str | None = None

    def iter_parts(self) -> Iterator[Part]:
        for uses in self.uses:
            if isinstance(uses, RandomChoices):
                yield from uses.weighted_random_with_replacement
            else:
                yield uses


@dataclass
class Sprite(object):
    element: etree._Element
    package_name: str
    texture: str
    ltwh: tuple[int, int, int, int]


@dataclass
class BaroItem(object):
    element: etree._Element
    identifier: Identifier
    nameidentifier: str | None
    tags: list[Identifier]


def extract_BaroItem(element) -> Iterator[BaroItem | Warning]:
    # there are a lot of attributes on this, we don't care to explicitly ignore
    # them so don't yield from attrs.warnings() since that warns us about
    # unused attributes
    attrs = Attribs.from_element(element)
    try:
        yield BaroItem(
            element=element,
            identifier=attrs.use("identifier", convert=make_identifier),
            nameidentifier=attrs.or_none("nameidentifier"),
            tags=attrs.use("tags", convert=split_identifier_list, default=[]),
        )
    except Error as err:
        yield err.as_warning()


def extract_Sprite_under(el):
    for child in skip_comments(el):
        if child.tag.lower() in ("inventoryicon", "sprite"):
            yield from extract_Sprite(child)


def extract_Sprite(el) -> Iterator[Sprite | Warning]:
    attrs = Attribs.from_element(el)

    try:
        # fmt: off
        if     (sheetindex := attrs.or_none("sheetindex", convert=split_int_pair)) \
           and (sheetelementsize := attrs.or_none("sheetelementsize", convert=split_int_pair)):
            (col, row) = sheetindex
            (w, h) = sheetelementsize
            ltwh = (w * col, row * h, w, h)

        else:
            ltwh = attrs.or_none("sourcerect", convert=split_ltwh) \
                or attrs.use("source", convert=split_ltwh)
        # fmt: on

        yield Sprite(
            element=el,
            texture=attrs.use("texture"),
            ltwh=ltwh,
            package_name=attrs.use(PACKAGE_ORIGIN_KEY),
        )
    except Error as err:
        yield err.as_warning()


VOWEL_PATTERN = re.compile(r"[aeiou]", flags=re.IGNORECASE)


def make_process_id(identifier: Identifier, tag: str, index: int):
    # try not to use identifier characters for a separator
    prefix = identifier[0] + VOWEL_PATTERN.sub("", identifier[1:])
    return f"{prefix}/{tag[0]}{index:x}"


def extract_Item(item) -> Iterator[Process | Warning]:
    identifier = extract_item_identifier(item)
    counts = defaultdict(count)  # type: ignore

    for el in skip_comments(item):
        tag = el.tag.lower()

        try:
            if tag == "fabricate":
                id = make_process_id(identifier, tag, next(counts[tag]))
                yield from extract_Fabricate(el, id=id)

            elif tag == "deconstruct":
                id = make_process_id(identifier, tag, next(counts[tag]))
                yield from extract_Deconstruct(el, id=id)

            elif tag == "price":
                id = make_process_id(identifier, tag, next(counts[tag]))
                yield from extract_Price(el, id=id)

        except Error as err:
            yield err.as_warning()


def extract_item_identifier(el) -> Identifier:
    identifier = el.get("identifier")

    if not identifier:
        raise MissingAttribute(attribute="identifier", element=el)

    return make_identifier(identifier)


def extract_Fabricate(el, **kwargs) -> Iterator[Process | Warning]:
    # <Fabricate> is a child of <Item> or whatever. Our model is upside down
    # compared to Barotrauma. Our Fabricate has the item it outs output as a
    # child in `uses`.

    attrs = Attribs.from_element(el)
    attrs.ignore(
        "fabricationlimitmin",
        "fabricationlimitmax",
        "quality",
        "outcondition",
        "hidefornontraitors",
    )

    res = Process(
        **kwargs,
        uses=[
            Part(
                what=extract_item_identifier(el.getparent()),
                amount=attrs.use("amount", convert=int, default=1),
            )
        ],
        skills={},
        stations=attrs.use("suitablefabricators", convert=split_identifier_list),
        time=attrs.use("requiredtime", convert=float, default=1.0),
        needs_recipe=attrs.use("requiresrecipe", default=False, convert=xmlbool),
        description=attrs.opt("displayname"),
    )

    requiredmoney = attrs.opt("requiredmoney", convert=int)
    if requiredmoney:  # probably buying from a vending machine
        res.uses.append(Part(what=MONEY, amount=-requiredmoney))

    yield from attrs.warnings()

    for child in el.xpath("*"):
        try:
            for item in extract_Fabricate_Item(child):
                if isinstance(item, Part):
                    res.uses.append(item)

                elif isinstance(item, dict):  # RequiredSkill ...
                    res.skills.update(item)

                else:
                    yield item
        except Error as err:
            yield err.as_warning()

    yield res


def extract_Fabricate_Item(el) -> Iterator[RequiredSkill | Part | Warning]:
    attrs = Attribs.from_element(el)

    if el.tag.lower() == "requiredskill":
        skill_identifier = attrs.use("identifier", convert=make_identifier)
        skill_level = attrs.use("level", convert=float)
        yield {skill_identifier: skill_level}

    elif el.tag.lower() in ("requireditem", "item"):
        attrs.ignore("usecondition", "header", "defaultitem")

        what = attrs.or_none("identifier", convert=make_identifier)
        if what is None:
            what = attrs.or_none("tag", convert=make_identifier)
        if what is None:
            raise MissingAttribute(attribute=("identifier", "tag"), element=el)

        # this is an ingredient/required item. it is consumed
        # during fabrication, so the amount is negative
        amount = -(
            attrs.or_none("amount", convert=int)
            or attrs.use("count", convert=int, default=1)
        )

        yield Part(
            what=what,
            amount=amount,
            condition=(
                attrs.or_none("mincondition", convert=float),
                attrs.or_none("maxcondition", convert=float),
            ),
            # description=attrs.or_none("description"),
        )

    else:
        yield warn_unexpected_element(unexpected=el)

    for child in el.xpath("*"):
        yield warn_unexpected_element(unexpected=child)


def extract_Deconstruct(el, **kwargs) -> Iterator[Process | Warning]:
    attrs = Attribs.from_element(el)

    fab = Process(
        **kwargs,
        uses=[Part(what=extract_item_identifier(el.getparent()), amount=-1)],
        skills={},
        time=attrs.use("time", convert=float, default=1.0),
        stations=attrs.use(
            "requireddeconstructor",
            default=[make_identifier("deconstructor")],
            convert=split_identifier_list,
        ),
    )

    if attrs.use("chooserandom", convert=xmlbool, default=False):
        # Weird special case for genetics detailed in extract_Deconstruct_Item() ...
        #
        # I don't want to model identifying unidentified genetic material the
        # way barotrauma does it because their way doesn't map to how players
        # understand it.
        #
        # So this peeks if all children under chooserandom have the same
        # requiredotheritem and "moves" it up in that case.
        children = iter(el.xpath("*"))
        if (
            (head := next(children, None)) is not None
            and (req := head.get("requiredotheritem")) is not None
            and all(req == sibling.get("requiredotheritem") for sibling in children)
        ):
            for item in extract_Deconstruct_Item(head):
                if isinstance(item, Part) and item.is_consumed:
                    break
            else:
                raise Exception("did not find requiredotheritem")

            for child in el.xpath("*"):
                child.attrib.pop("requiredotheritem")

            fab.uses.append(item)

        choose = RandomChoices(
            weighted_random_with_replacement=[],
            amount=attrs.use("amount", convert=int, default=1),
        )
        fab.uses.append(choose)
    else:
        choose = None

    for child in el.xpath("*"):
        try:
            for item in extract_Deconstruct_Item(child):
                if isinstance(item, Part):
                    if item.is_created:
                        if choose is None:
                            fab.uses.append(item)
                        else:
                            choose.weighted_random_with_replacement.append(item)
                    elif item.is_consumed:
                        if choose is None:
                            fab.uses.append(item)
                        else:
                            yield Warning(
                                "requiredotheritem with chooserandom not handled ",
                                element=el,
                            )
                            return  # skip this Deconstruct entirely
                    else:
                        yield Warning("item has no amount", item=item, element=child)
                else:
                    yield item
        except Error as err:
            yield err.as_warning()

    yield fab

    yield from attrs.warnings()


def extract_Deconstruct_Item(el) -> Iterator[Part | Warning]:
    attrs = Attribs.from_element(el)
    attrs.ignore(
        "commonness",
        "copycondition",
        # useful but confusing to display vs the condition the input is
        # required to be at for this part to be produced Items/ItemPrefab.cs:62
        "outcondition",
        "outconditionmin",
        "outconditionmax",
        "activatebuttontext",
        "infotext",
        "infotextonotheritemmissing",
        # saw multiplier once Items/Genetic/genetic.xml:224 dunno what it means
        #   <Item name="" identifier="geneticmaterialhusk" variantof="geneticmaterialcrawler" nameidentifier="geneticmaterial">
        #     <Deconstruct>
        #       <Item identifier="geneticmaterialhusk" multiplier="5" />
        "multiplier",
    )

    if el.tag.lower() in (
        "requireditem",  # not to be confused with requiredotheritem lulz
        "item",
    ):
        # Deconstruction Items are yields, so positive amounts
        # mincondition maxcondition constrain whether that item qualifies to be
        # yielded as an output,
        yield Part(
            what=attrs.use("identifier", convert=make_identifier),
            amount=attrs.use("amount", convert=int, default=1),
            condition=(
                attrs.or_none("mincondition", convert=float),
                attrs.or_none("maxcondition", convert=float),
            ),
            # description=attrs.or_none("description"),
        )

        # This is super fucking stupid.
        #
        # The game models identifying genetic material as _deconstructing_
        # unidentified genetic material into into usable genetic material. But
        # it also wants to consume saline in the process. But deconstruction
        # doesn't consume more than one item -- that's what fabrication is for.
        #
        # Instead, there's this requiredotheritem attribute on the <Item> in a
        # <Deconstruct> that makes that deconstruction recipe require another
        # input to consume.
        #
        # Also, all the recipes for identifying genetic material require the
        # same item; stabilozine. But, instead of doing something like...
        #
        #   <Deconstruct>
        #     <RequiredOtherItem identifier="stabilozine">
        #     <RandomChoice>
        #       <Item identifier="geneticmaterialcrawler" commonness="3" outconditionmin="0.1" ... />
        #       <Item identifier="geneticmaterialcrawler" commonness="2" outconditionmin="0.2" ... />
        #       <Item identifier="geneticmaterialcrawler" commonness="1" outconditionmin="0.4" ... />
        #       <Item identifier="geneticmaterialmudraptor" commonness="3" outconditionmin="0.1" ... />
        #       <Item identifier="geneticmaterialmudraptor" commonness="2" outconditionmin="0.2" ... />
        #       <Item identifier="geneticmaterialmudraptor" commonness="1" outconditionmin="0.4" ... />
        #       ...
        #     </RandomChoice>
        #   </Deconstruct>
        #
        # ... the RandomChoice is a property of the entire deconstruct and each
        # item has to specify the stabilozine as a requiredotheritem.
        #
        #   <Deconstruct chooserandom="true">
        #     <Item identifier="geneticmaterialcrawler" commonness="3" requiredotheritem="stabilozine" outconditionmin="0.1" ... />
        #     <Item identifier="geneticmaterialcrawler" commonness="2" requiredotheritem="stabilozine" outconditionmin="0.2" ... />
        #     <Item identifier="geneticmaterialcrawler" commonness="1" requiredotheritem="stabilozine" outconditionmin="0.4" ... />
        #     <Item identifier="geneticmaterialmudraptor" commonness="3" requiredotheritem="stabilozine" outconditionmin="0.1" ... />
        #     <Item identifier="geneticmaterialmudraptor" commonness="2" requiredotheritem="stabilozine" outconditionmin="0.2" ... />
        #     <Item identifier="geneticmaterialmudraptor" commonness="1" requiredotheritem="stabilozine" outconditionmin="0.4" ... />
        #     ...
        #   </Deconstruct>
        #
        # But this isn't how people think about it. Like a possible yield
        # conditional on an extra input. That doesn't make sense here because
        # they all take the same input.
        #
        # Combining genetics is also a Deconstruct with requiredotheritem. But
        # less fucky since chooserandom is not involved.

        other = attrs.or_none("requiredotheritem")
        if other is not None:
            yield Part(what=make_identifier(other), amount=-1)

    else:
        yield warn_unexpected_element(unexpected=el)

    yield from attrs.warnings()


def extract_Price(el, **kwargs) -> Iterator[Process | Warning]:
    attrs = Attribs.from_element(el)

    # canbespecial is for discounts or in demand i guess? could be interesting
    # requiresunlock not sure how to display this but would be useful
    attrs.ignore(
        "minleveldifficulty",
        "minavailable",
        "maxavailable",
        "canbespecial",
        "displaynonempty",
        "requiresunlock",
    )
    # these are probably important if showing/guessing prices
    attrs.ignore("baseprice", "buyingpricemodifier", "multiplier")

    # price = attrs.use("baseprice", convert=float, default=0.0)
    # multiplier = attrs.use("multiplier", convert=float, default=1.0)

    is_sold_by_stores_generally = attrs.use("sold", convert=xmlbool, default=None)
    if is_sold_by_stores_generally is None:
        is_sold_by_stores_generally = attrs.use(
            "soldbydefault", convert=xmlbool, default=True
        )

    yield from attrs.warnings()

    price = Process(
        **kwargs,
        stations=[],
        uses=[
            Part(what=MONEY, amount=-1),
            Part(what=extract_item_identifier(el.getparent()), amount=1),
        ],
        skills={},
    )

    for child in el.xpath("*"):

        if child.tag.lower() != "price":
            yield warn_unexpected_element(unexpected=child)
            continue

        attrs = Attribs.from_element(child)
        attrs.ignore(
            "multiplier",
            "minavailable",
            "maxavailable",
            "mindifficulty",
            "minleveldifficulty",
        )

        try:
            locationtype = attrs.opt("locationtype", convert=make_identifier)
            if locationtype:
                stations_fallback = dict(default=[f"merchant{locationtype}"])
            else:
                stations_fallback = dict()
            stations = attrs.use(
                "storeidentifier", convert=split_identifier_list, **stations_fallback
            )

            if attrs.use("sold", convert=xmlbool, default=is_sold_by_stores_generally):
                price.stations.extend(stations)

        except Error as err:
            yield err.as_warning()

        yield from attrs.warnings()

    if price.stations:
        yield price


@dataclass
class ContentPackageHeader(object):
    name: str
    iscorepackage: bool
    gameversion: str
    modversion: str
    steamworkshopid: str


@dataclass
class ContentPackage(object):
    path: Path
    xmlpath: Path
    element: etree._Element
    name: str
    iscorepackage: bool
    gameversion: str
    modversion: str
    steamworkshopid: str


@dataclass
class PreItem(object):
    """variant_of not applied"""

    package: ContentPackage
    xmlpath: Path
    element: etree._Element
    identifier: Identifier
    variant_of: Identifier | None


def find_ContentPackage_element(xmlpath: Path, peek=512) -> etree._Element | None:
    with xmlpath.open("rb") as file:
        if peek:
            if b"<contentpackage" not in file.read(peek).lower():
                return None

            file.seek(0)

        for _, element in etree.iterparse(file, events=("start",)):

            if element.tag.lower() == "contentpackage":
                return element

            else:
                break  # only check the root element

        return None


def extract_ContentPackageHeader(element) -> Iterator[ContentPackageHeader | Warning]:
    attrs = Attribs.from_element(element)
    attrs.ignore("altnames", "expectedhash")

    yield ContentPackageHeader(
        name=attrs.use("name"),
        iscorepackage=attrs.use("corepackage", convert=xmlbool, default=False),
        gameversion=attrs.use("gameversion"),
        modversion=attrs.use("modversion", default=None),
        steamworkshopid=attrs.use("steamworkshopid", default=None),
    )

    yield from attrs.warnings()


@dataclass
class ContentPath(object):
    kind: Literal["item"] | Literal["text"]
    path: Path


def extract_ContentPath(element, convert_path) -> Iterator[ContentPath | Warning]:
    for child in element:
        tag = child.tag.lower()

        if tag not in ("item", "text"):
            continue

        a = Attribs.from_element(child)

        try:
            path = a.use("file", convert=convert_path)

        except Error as err:
            yield err.as_warning()

        else:
            yield ContentPath(kind=tag, path=path)

        yield from a.warnings()


def apply_variants(
    preitems: dict[Identifier, PreItem]
) -> Iterator[tuple[Identifier, etree._Element] | Warning]:
    graph: dict[Identifier, set[Identifier]] = {}

    for identifier, preitem in preitems.items():

        if preitem.variant_of is None:
            yield identifier, preitem.element

        elif preitem.variant_of not in preitems:
            yield Warning(
                "element's variant_of not not found",
                element=preitem.element,
                variant_of=preitem.variant_of,
                path=preitem.xmlpath,
            )

        else:
            graph[identifier] = {preitem.variant_of}

    # topological sort so that identifiers for variants of an item are iterated
    # _after_ the identifiers of the item they are a variant of ...
    #
    # so B =variant_of=> A yields A before B
    applied: dict[Identifier, etree._Element] = {}

    for identifier in TopologicalSorter(graph).static_order():

        # anything that isn't a variant was already yielded in the earlier loop
        if (variation := preitems[identifier]).variant_of is None:
            continue

        # fmt: off
        base_element = applied.get(variation.variant_of) \
                    or preitems[variation.variant_of].element
        # fmt: on

        applied_element = applied[identifier] = apply_variant(
            base_element,
            variation.element,
            only_tags=("fabricate", "deconstruct", "price", "inventoryicon", "sprite"),
        )

        yield identifier, applied_element


def apply_variant(
    base: etree._Element, variant: etree._Element, only_tags=()
) -> etree._Element:
    """Given <variant variantof=base>, apply variant over top of base, returning a new element.

    variantof is some sort of "inheritance" or reuse gimick where some element
    can be a variant of another and that element's definition is merged over
    that of the thing it's a variant of.

    mostly, the game copies the referenced element and then adds or replaces
    existing attributes from the variant; working recursively by pairing child
    elements by their tag name

    some uh ... "features":

    - some element attribute values are numbers prefixed with * or +, those are
      added or multiplied with the existing value i guess but i don't think
      this feature is used? so I'm ignoring it

    - if an element in a variant has no children or attributes, it removes the
      element instead of merging???

    - encounering the <Clear/> element in the variant removes all children

    see BarotraumaShared/SharedSource/PreItems/IImplementsVariants.cs
    """
    applied = etree.Element(variant.tag)
    applied.sourceline = variant.sourceline  # kind of a lie ?
    # mypy upset that we're passing a generator???
    applied.attrib.update((k.lower(), v) for k, v in base.attrib.iteritems())  # type: ignore
    applied.attrib.update((k.lower(), v) for k, v in variant.attrib.iteritems())  # type: ignore
    # lxml typings need to calm down
    applied.attrib.pop("variantof", None)  # type: ignore
    applied.attrib.pop("inherit", None)  # type: ignore

    variant_children: list[etree._Element | None] = list(skip_comments(variant))

    # seems to be a funny special case where <Clear/> is used to produce an
    # element with no children from either the base or the variant
    if any(c.tag.lower() == "clear" for c in variant_children):  # type: ignore
        return applied

    for base_child in skip_comments(base):

        child_tag = base_child.tag.lower()

        # optimization, sometimes only merge elements if their tag matches
        # something the caller cares about
        if only_tags and child_tag not in only_tags:
            continue

        for i, variant_child in enumerate(variant_children):
            if variant_child is not None and child_tag == variant_child.tag.lower():
                variant_children[i] = None

                # if the variant element has no attributes or children, this
                # omits the element in the output instead of merging the
                # element pair
                if element_is_non_empty(variant_child):
                    applied.append(apply_variant(base_child, variant_child))

                break

        else:
            applied.append(copy(base_child))

    applied.extend(c for c in variant_children if c is not None)

    return applied


def tidy_processes(processes: Iterator[Process]) -> Iterator[Process]:
    for process in processes:

        process.uses.sort(key=lambda p: p.amount)

        # if parts consumed or produced are listed twice with the same
        # information, combine their amounts into one item

        for i, part in enumerate_rev(process.uses[:-1]):

            if isinstance(part, RandomChoices):
                continue

            for j, other in enumerate_rev(process.uses[i + 1 :]):

                if isinstance(other, RandomChoices):
                    continue

                if part.can_combine(other):
                    part.combine_in_place(other)
                    del process.uses[i + 1 + j]

        yield process


def retain_only_process_items(
    index: dict[Identifier, BaroItem], processes: list[Process]
) -> dict[Identifier, BaroItem]:
    identifiers_used: set[Identifier | Money] = set()

    for process in processes:
        identifiers_used.update(part.what for part in process.iter_parts())
        identifiers_used.update(process.stations)

    return {
        identifier: item
        for identifier, item in index.items()
        if identifier in identifiers_used
        or any(tag in identifiers_used for tag in item.tags)
    }


def drop_prefix(text, prefix):
    if text.startswith(prefix):
        return text[len(prefix) :]


class Attribs(dict):
    __element: etree._Element
    __missing: list[str]

    @classmethod
    def from_element(cls, element) -> "Attribs":
        self = cls(((k.lower(), v) for k, v in element.attrib.iteritems()))
        self.__element = element
        self.__missing = []
        return self

    class __attribute_not_found(object):
        pass

    class __raise(object):
        pass

    class __warn(object):  # depr?
        pass

    def ignore(self, *attributes):
        for a in attributes:
            self.pop(a, None)

    T = TypeVar("T")

    @overload
    def use(self, attribute, *, convert: Callable[[str], T], default=__raise) -> T:
        pass

    @overload
    def use(self, attribute, *, convert=None, default=__raise) -> str:
        pass

    def use(self, attribute, *, convert=None, default=__raise):
        value = self.pop(attribute, self.__attribute_not_found)

        if value is self.__attribute_not_found:

            if default is self.__warn:
                self.__missing.append(attribute)
                return self.__attribute_not_found  # returning "private" value FIXME

            elif default is self.__raise:
                raise MissingAttribute(attribute=attribute, element=self.__element)

            else:
                return default

        if convert is not None:
            try:
                value = convert(value)
            except ValueError as err:
                raise BadValue(
                    error=err, attribute=attribute, element=self.__element
                ) from err

        return value

    def opt(self, attribute, *, convert=None):
        return self.use(attribute, convert=convert, default=None)

    or_none = opt

    def warnings(self) -> Iterator[Warning]:
        if self:
            yield Warning(
                "attributes on element were not used",
                attributes=tuple(self.keys()),
                element=self.__element,
            )

        if self.__missing:
            yield warn_missing_attribute(
                attribute=self.__missing, element=self.__element
            )


T = TypeVar("T")


def enumerate_rev(l: list[T]) -> Iterator[tuple[int, T]]:
    """
    >>> list(enumerate_rev(['foo', 1, True]))
    [(2, True), (1, 1), (0, 'foo')]
    """
    return zip(count(len(l) - 1, -1), reversed(l))


def sign(i: int):
    return 1 if i >= 0 else -1


def partition(it, fn):
    l, r = [], []
    for i in it:
        (l if fn(i) else r).append(i)
    return l, r


def skip_comments(el: etree._Element) -> Iterator[etree._Element]:
    return (child for child in el if child.tag is not etree.Comment)


def element_is_non_empty(el: etree._Element) -> bool:
    return bool(el.attrib or len(el))


from threading import Lock


def resolve_path_with_relative_fallback(
    path: str,
    *,
    vanilla: ContentPackage,
    current: ContentPackage,
    fallback: Path,
) -> Path:
    _resolve = partial(resolve_path, vanilla=vanilla, current=current)
    try:
        return _resolve(path)
    except FileNotFoundError:
        # if this raises a value error because the given paths aren't relative
        # to each other then that's probably a good thing
        qualified = fallback.relative_to(current.path)
        return _resolve(qualified / path)


# {(package path, suffix): {lowercase path: filesystem path}}
__PATH_CACHE: dict[tuple[Path, str], dict[str, Path]] = {}


def resolve_path(
    path: str,
    *,
    vanilla: ContentPackage,
    current: ContentPackage,
) -> Path:
    """
    given a path to a resouce in a content package, find the corresponding file
    on the filesystem

    raises FileNotFoundError otherwise

    should be case insensitive, should not resolve outside of `root`, should
    return a path that is a file that exists

    also, this sort of expects you to pass it Barotrauma's Content directory
    for the Vanilla package, but Vanilla resource paths are prefixed with
    "content/" so those prefixes are dropped in that case
    """
    suffix = Path(path).suffix

    # FIXME not cross platform ...
    path = str(path).replace("\\", "/")

    if relative_path := is_mod_relative_path(path):
        if vanilla == current:
            raise Exception("%ModDir% used in Vanilla??")
        package_path = current.path
        content_path = relative_path.lower()

    else:
        package_path = vanilla.path
        content_path = path.lower()
        if package_path.parts[-1].lower() == "content":
            if trimmed := drop_prefix(content_path, "content/"):
                content_path = trimmed

    paths = __PATH_CACHE.get((package_path, suffix))
    if paths is None:
        paths = __PATH_CACHE[(package_path, suffix)] = {
            str(p.relative_to(package_path)).lower(): p
            for p in package_path.rglob(f"*{suffix}")
        }

    if content_path not in paths:
        raise FileNotFoundError(package_path / content_path)

    realpath = paths[content_path]

    assert realpath.resolve().is_relative_to(package_path)

    return realpath


def is_mod_relative_path(path: str) -> str | None:
    if path.startswith("%ModDir:"):  # for specifying a mod by name
        raise NotImplementedError(path)
    return drop_prefix(path, "%ModDir%/")


def ltwh_to_ltbr(ltwh: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    (l, t, w, h) = ltwh
    return (l, t, l + w, t + h)


__SPRITE_CACHE_LOCK = Lock()
__SPRITE_CACHE: dict[Path, "PIL.Image.Image"] = {}
__SPRITE_CACHE_IMAGE_LOCKS: dict[Path, Lock] = {}


def load_sprite_at_path(
    path: Path, ltwh: tuple[int, int, int, int]
) -> "PIL.Image.Image":
    from PIL import Image

    with __SPRITE_CACHE_LOCK:
        image_lock = __SPRITE_CACHE_IMAGE_LOCKS.get(path)
        if image_lock is None:
            image_lock = __SPRITE_CACHE_IMAGE_LOCKS[path] = Lock()

    with image_lock:
        # A BIT FUCKY?
        image = __SPRITE_CACHE.get(path)
        if image is None:
            image = __SPRITE_CACHE[path] = Image.open(path)
        image = image.copy()

    image = image.crop(ltwh_to_ltbr(ltwh))  # crop to sprite in sheet
    image = image.crop(image.getbbox())  # crop transparency
    # copy done earlier because threading
    # image = image.copy()  # thumbnail() is in-place returns None, copy first
    image.thumbnail((48, 48))
    return image


def to_base64(image: "PIL.Image.Image", format="webp") -> str:
    buf = BytesIO()
    image.save(buf, format=format)
    return b64encode(buf.getvalue()).decode()


def load_xmls(paths: list[Path]) -> Iterator[tuple[Path, etree._Document]]:
    for path in paths:
        try:
            with path.open() as file:
                doc = etree.parse(file)

        except (OSError, etree.Error) as err:
            log_warning(err, file=file)
            continue

        else:
            yield path, doc


def main() -> None:
    from argparse import ArgumentParser

    # fmt: off
    parser = ArgumentParser()
    parser.add_argument("--package", nargs="*", action="append")
    parser.add_argument("--content", nargs="+", type=Path, required=True, help="path to barotrauma Content or workshop directory containing filelist.xml")
    parser.add_argument("--output", nargs='?', type=Path, help="path to write package css and json files")
    # fmt: on

    args = parser.parse_args()

    logtime("finding contentpackage")

    # the ordering of --content is not important
    # but the order of --package is

    package_me: list[list[ContentPackage]]
    packages: list[ContentPackage]

    packages = list(log_warnings(_rglob_for_ContentPackages(args.content)))
    logtime(f"packages: {', '.join(package.name for package in packages)}")

    # sanity checks

    vanilla = _find_core_package_or_exit(packages)
    package_me = _validate_load_order_or_exit(vanilla, packages, args.package)

    # parse item xml; read identifier and variantof

    logtime("reading item identifiers...")

    preitems: dict[str, dict[Identifier, PreItem]] = {}
    alltexts: dict[str, list[InfoTexts]] = {}

    for package in packages:
        items, texts = _resolve_content_package_paths(vanilla, package)

        _index = preitems[package.name] = {}
        for preitem in _iter_content_package_preitems(package, items):
            _index[preitem.identifier] = preitem
        logtime(f"{package.name} » {len(_index)} items")

        _texts = alltexts[package.name] = []
        _texts.extend(_iter_content_package_infotexts(package, texts))
        logtime(f"{package.name} » {len(_texts)} texts")

    # build bundles for output

    bundles: list[Bundle] = []

    for load_order in package_me:
        logtime(f"bundling {[p.name for p in load_order]}")
        bundles.append(build_bundle(load_order, preitems, alltexts))

    if not args.output:
        log_warning("no --output path specified, not writing anything!")
        return

    # write bundles under output

    args.output.mkdir(parents=True, exist_ok=True)

    index_path = args.output / "index.ts"
    with index_path.open("w") as index:

        print(f"/* generated by baro-data.py */", file=index)

        for i, bundle in enumerate(bundles):
            logtime(f"writing {bundle}")

            name = mangled_filename(
                *(f"{p.name}-{p.version}" for p in bundle.load_order)
            )
            bundle_path = (args.output / name).with_suffix(".json")
            css_path = (args.output / name).with_suffix(".css")

            css_path.open("w").write(bundle.sprites_css.getvalue())
            logtime(f"wrote {css_path}")

            dumpme = {
                "name": name,
                "load_order": bundle.load_order,
                "tags_by_identifier": bundle.tags_by_identifier,
                "package_entities": bundle.package_entities,
                "processes": bundle.processes,
                "i18n": bundle.i18n,
            }
            json.dump(dumpme, default=serialize_dataclass, fp=bundle_path.open("w"))
            logtime(f"wrote {bundle_path}")

            # for language_name, dictionary in bundle.i18n.items():
            #     language_path = path_nosuffix.with_name(
            #         f"{path_nosuffix.name}_{mangled_filename(language_name)}.json"
            #     )
            #     json.dump(dictionary, fp=language_path.open("w"))
            #     logtime(f"wrote {language_path}")

            print(
                f'import {{ load_order as load_order{i}, name as name{i}, }} from "./{bundle_path.name}"',
                file=index,
            )
            print(f'import bundle{i} from "./{bundle_path.name}?url"', file=index)
            print(f'import sprites{i} from "./{css_path.name}?url"', file=index)

        print("export const BUNDLES: LoadableBundle[] = [", file=index)
        for i, bundle in enumerate(bundles):
            pass
            print(
                "{ "
                "name: name%(i)d, "
                "load_order: load_order%(i)d, "
                "url: bundle%(i)d, "
                "sprites: sprites%(i)d, "
                "}," % {"i": i},
                file=index,
            )
        print("]\n", file=index)

        print(TYPES_TS, file=index)

    logtime(f"wrote {index_path}")


def _rglob_for_ContentPackages(paths: list[Path]) -> Iterator[ContentPackage | Warning]:
    for path in paths:
        for xmlpath in path.rglob("*.xml"):
            element = find_ContentPackage_element(xmlpath)
            if element is not None:
                for item in extract_ContentPackageHeader(element):
                    if isinstance(item, Warning):
                        yield item.with_path(xmlpath)
                    else:
                        yield ContentPackage(
                            path=path, xmlpath=xmlpath, element=element, **asdict(item)
                        )


def _find_core_package_or_exit(packages: list[ContentPackage]) -> ContentPackage:
    is_core, non_core = partition(packages, lambda p: p.iscorepackage)

    if len(is_core) != 1:
        log_warning(
            "expected exactly one contentpackage to be marked a corepackage",
            iscorepackage=set(p.name for p in is_core),
            notcorepackage=set(p.name for p in non_core),
        )
        raise SystemExit(1)

    return is_core[0]


def _validate_load_order_or_exit(
    vanilla: ContentPackage,
    packages: list[ContentPackage],
    name_load_order_list: list[list[str]] | None,
) -> list[list[ContentPackage]]:
    if not name_load_order_list:
        return [[vanilla]]

    package_load_order_list = []
    package_by_name = {package.name: package for package in packages}
    package_names = (package.name for package in packages)

    if missing := set(chain.from_iterable(name_load_order_list)) - set(package_names):
        log_warning(
            "some requested packages were not found under --content",
            missing=missing,
        )
        raise SystemExit(1)

    for name_load_order in name_load_order_list:
        if not name_load_order:
            continue

        package_load_order = [package_by_name[name] for name in name_load_order]
        if package_load_order[0].iscorepackage:
            pass

        elif any(p.iscorepackage for p in package_load_order):
            log_warning(
                "corepackage found in load order but not the first item",
                shouldbefirst=vanilla,
            )
            raise SystemExit(1)

        else:
            package_load_order = [vanilla] + package_load_order

        package_load_order_list.append(package_load_order)

    return package_load_order_list


def _resolve_content_package_paths(
    vanilla: ContentPackage, package: ContentPackage
) -> tuple[list[Path], list[Path]]:
    """returns (items, texts)"""
    items: list[Path] = []
    texts: list[Path] = []

    convert_path = partial(resolve_path, vanilla=vanilla, current=package)
    content_paths = extract_ContentPath(package.element, convert_path)

    for content in log_warnings(content_paths, path=package.xmlpath):
        if content.kind == "item":
            items.append(content.path)
        elif content.kind == "text":
            texts.append(content.path)
        else:
            assert False, content.kind

    return items, texts


def _iter_content_package_preitems(
    package: ContentPackage, item_paths: list[Path]
) -> Iterator[PreItem]:
    for xmlpath, doc in load_xmls(item_paths):
        for item in extract_ItemHeader(doc):

            # hack when loading sprites to know what context a %ModDir% was
            # used in; this must survive apply_variant and not break it
            #
            # it's quite spooky and file paths containing %ModDir% should
            # probably almost be preprocessed earlier on but i don't know how
            # this stuff works; it's not well documented the people on discord
            # don't know shit
            for element in skip_comments(item.element):
                if element.tag.lower() in (
                    "inventoryicon",
                    "sprite",
                ) and element_is_non_empty(element):
                    element.attrib[PACKAGE_ORIGIN_KEY] = package.name

            yield PreItem(
                package=package,
                xmlpath=xmlpath,
                element=item.element,
                identifier=item.identifier,
                variant_of=item.variant_of,
            )


@dataclass
class InfoTexts(object):
    package: ContentPackage
    language: str
    dictionary: dict[str, str]


def _iter_content_package_infotexts(
    package: ContentPackage, text_paths: list[Path]
) -> Iterator[InfoTexts]:
    for xmlpath, doc in load_xmls(text_paths):

        # TODO this assumes that root.tag == infotexts I guess?
        root = doc.getroot()

        if not (language := root.get("language")):
            continue

        # TODO warn about duplicates?
        dictionary = dict(_iter_infotext_items(root))

        if language not in dictionary and (language_name := root.get("translatedname")):
            dictionary[language] = language_name

        yield InfoTexts(package=package, language=language, dictionary=dictionary)


def _iter_infotext_items(element: etree._Element) -> Iterator[tuple[str, str]]:
    for child in skip_comments(element):
        tag = child.tag.lower()

        if tag == "override":
            yield from _iter_infotext_items(child)

        elif not child.text:
            pass

        elif tag == "credit":
            yield "$", child.text

        elif tag in ("fabricatorrequiresrecipe", "random"):
            yield tag, child.text

        else:
            # fmt: off
            msg = (   drop_prefix(tag, "entityname.")
                   or drop_prefix(tag, "npctitle.") # merchants
                   or drop_prefix(tag, 'fabricationdescription.')) # munition_core etc
            # fmt: on
            yield msg, child.text


@dataclass
class ItemHeader(object):
    element: etree._Element
    identifier: Identifier
    variant_of: Identifier | None


def extract_ItemHeader(doc) -> Iterator[ItemHeader]:
    root = doc.getroot()
    element_tag = root.tag.lower()

    if element_tag == "item":
        yield from _extract_element_ItemHeader([root])

    elif element_tag == "items":
        yield from _extract_element_ItemHeader(root)


def _extract_element_ItemHeader(items) -> Iterator[ItemHeader]:
    for item in skip_comments(items):

        if item.tag.lower() == "override":
            yield from _extract_element_ItemHeader(item)
            continue

        a = Attribs.from_element(item)

        if not (identifier := a.opt("identifier", convert=make_identifier)):
            continue

        yield ItemHeader(
            element=item,
            identifier=identifier,
            variant_of=a.opt("variantof", convert=make_identifier),
        )


@dataclass
class BundlePackageMeta(object):
    name: str
    version: str | None
    steamworkshopid: str | None


@dataclass
class Bundle(object):
    # should be ordered with Vanilla (or core package) at the first index
    load_order: list[BundlePackageMeta]
    tags_by_identifier: dict[Identifier, list[Identifier]]
    package_entities: dict[str, list[Identifier]]
    processes: list[Process]
    # {language: {identifier: humantext}}
    i18n: dict[str, dict[str, str]]
    sprites_css: StringIO

    def __str__(self):
        return ", ".join(l.name for l in self.load_order)


FILENAME_MANGLE_PATTERN = re.compile(r"[^a-z0-9]", flags=re.IGNORECASE)


def mangled_filename(*parts: str) -> str:
    return "+".join(FILENAME_MANGLE_PATTERN.sub("-", p) for p in parts)[:128]


def build_bundle(
    load_order: list[ContentPackage],
    preitems_by_package: dict[str, dict[Identifier, PreItem]],
    texts_by_package: dict[str, list[InfoTexts]],
) -> Bundle:

    logtime("applying variants")

    vanilla = load_order[0]
    package_by_name = {package.name: package for package in load_order}

    preitem_layers = (preitems_by_package[package.name] for package in load_order)

    preitem_by_identifier: dict[Identifier, PreItem]
    preitem_by_identifier = reduce(ior, preitem_layers, {})  # ior is |=,

    index: dict[Identifier, BaroItem] = {}
    for identifier, element in log_warnings(apply_variants(preitem_by_identifier)):
        for baro_item in log_warnings(extract_BaroItem(element)):
            index[identifier] = baro_item

    logtime("reading processes from items")

    processes: list[Process] = []

    for item in index.values():
        xmlpath = preitem_by_identifier[item.identifier].xmlpath
        processes.extend(
            tidy_processes(log_warnings(extract_Item(item.element), path=xmlpath))
        )

    # for i, process in enumerate_rev(processes):
    #     for j, other in enumerate_rev(processes[i + 1 :]):
    #         if process.id == other.id:
    #             log_warning("processes share the same `id`", l=process, r=other)

    logtime(f"pruning {len(index)} items for {len(processes)} processes")

    index = retain_only_process_items(index, processes)

    logtime(f"retained {len(index)} items; generating sprites")

    sprites_css = _sprite_sheet_css(
        index.values(), vanilla, package_by_name, preitem_by_identifier
    )

    logtime(f"sprite sheet {len(sprites_css.getvalue().encode())} bytes")

    should_localize: set[str] = _should_localize_from_processes(processes, index)
    should_localize.update(("$", "fabricatorrequiresrecipe", "random"))

    # {language: {identifier: humantext}}
    i18n: dict[str, dict[str, str]] = _bundle_i18n(
        load_order, texts_by_package, should_localize
    )

    # TODO warn about duplicates?
    # if (current := dictionary.get(msg)) is not None and current != child.text:
    #     log_warning(
    #         "l10n duplicate",
    #         language=language,
    #         msg=msg,
    #         current=current,
    #         update=child.text,
    #     )

    # dictionary[msg] = child.text

    if _CHECK_L10N_MISSING:
        for language, dictionary in i18n.items():
            if not_found := should_localize - set(dictionary.keys()):
                log_warning("l10n not found", language=language, not_found=not_found)

    for lang, dictionary in i18n.items():
        logtime(f"{len(dictionary)} in {lang}")

    package_entities: dict[str, list[Identifier]] = {}
    for preitem in preitem_by_identifier.values():
        if not preitem.package.iscorepackage:
            # fmt: off
            package_entities.setdefault(preitem.package.name.lower(), []) \
                .append(preitem.identifier)
            # fmt: on

    return Bundle(
        load_order=[
            BundlePackageMeta(
                name=package.name,
                version=package.gameversion
                if package.iscorepackage
                else package.modversion,
                steamworkshopid=package.steamworkshopid,
            )
            for package in load_order
        ],
        tags_by_identifier={
            identifier: item.tags for identifier, item in index.items() if item.tags
        },
        package_entities=package_entities,
        processes=processes,
        i18n=i18n,
        sprites_css=sprites_css,
    )


def _sprite_sheet_css(
    items: Iterable[BaroItem],
    vanilla: ContentPackage,
    package_by_name: dict[str, ContentPackage],
    preitem_by_identifier: dict[Identifier, PreItem],
) -> StringIO:

    sprites_css = StringIO()

    if _CHECK_SPRITE_DUPE:
        dupes = {}  # type: ignore

    # as of python 3.8, the default max workers maxes out at 32 or something so
    # it doesn't act stupid on many-core machines
    with ThreadPoolExecutor() as ex:
        pending = {}

        for item in items:
            for sprite in log_warnings(extract_Sprite_under(item.element)):
                break
            else:
                log_warning("no sprite found", item=item)
                continue

            package = package_by_name[sprite.package_name]
            xmlpath = preitem_by_identifier[item.identifier].xmlpath

            try:
                texture_path = resolve_path_with_relative_fallback(
                    sprite.texture,
                    vanilla=vanilla,
                    current=package,
                    fallback=xmlpath.parent,
                )
            except FileNotFoundError as error:
                log_warning(
                    "texture not found",
                    error=error,
                    element=sprite.element,
                    path=xmlpath,
                )

            if _CHECK_SPRITE_DUPE:
                if (texture_path, sprite.ltwh) in dupes:
                    log_warning(
                        "dupe",
                        item=(texture_path, sprite.ltwh, item),
                        dupe=dupes[(texture_path, sprite.ltwh)],
                    )
                else:
                    dupes[(texture_path, sprite.ltwh)] = (
                        texture_path,
                        sprite.ltwh,
                        item,
                    )

            future = ex.submit(_load_base64_sprite_at_path, texture_path, sprite.ltwh)
            pending[future] = item.identifier

        for done in as_completed(pending):
            identifier = pending.pop(done)
            try:
                b64 = done.result()
            except Exception as error:
                log_warning("_load_sprite_at_path_as_base64", error=error)
            else:
                print(
                    '[data-sprite="%s"] { background: url("data:image/webp;base64,%s") }'
                    % (identifier, b64),
                    file=sprites_css,
                )

    return sprites_css


def _load_base64_sprite_at_path(path: Path, ltwh: tuple[int, int, int, int]):
    image = load_sprite_at_path(path, ltwh)
    return to_base64(image)


def _should_localize_from_processes(
    processes: list[Process],
    index: dict[Identifier, BaroItem],
) -> set[str]:
    should_localize: set[str] = set()

    for process in processes:
        should_localize.update(process.stations)

        for part in process.iter_parts():
            if part.what == MONEY:
                continue
            item = index.get(part.what)  # type: ignore
            if item and item.nameidentifier:
                should_localize.add(item.nameidentifier)
            else:
                should_localize.add(part.what)

    return should_localize


def _bundle_i18n(
    load_order: list[ContentPackage],
    texts_by_package: dict[str, list[InfoTexts]],
    should_localize: set[str],
) -> dict[str, dict[str, str]]:
    i18n: dict[str, dict[str, str]] = {}

    for package in reversed(load_order):
        for text in texts_by_package.get(package.name, ()):
            our_dictionary = i18n.setdefault(text.language, {})
            for msg in should_localize:
                if msg not in our_dictionary and msg in text.dictionary:
                    our_dictionary[msg] = text.dictionary[msg]

    return i18n


if __name__ == "__main__":
    main()
