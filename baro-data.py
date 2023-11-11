import io
import json
import os
import re
import sys
from base64 import b64encode
from copy import copy
from dataclasses import dataclass, is_dataclass, fields, field
from graphlib import TopologicalSorter
from itertools import count, chain
from lxml import etree
from operator import itemgetter
from pathlib import Path
from typing import (
    Union,
    NewType,
    TypeAlias,
    Callable,
    Iterator,
    overload,
    TypeVar,
    Literal,
    TYPE_CHECKING,
)
from types import MappingProxyType
from time import monotonic_ns

if TYPE_CHECKING:
    import PIL


class ansi:
    """256-color mode https://en.wikipedia.org/wiki/ANSI_escape_code#8-bit"""

    class Color(object):

        reset = "\x1b[0m"

        def __init__(self, n):
            self.fg = f"\x1b[38;5;{n}m"

        if sys.stderr.isatty() and "NOCOLOR" not in os.environ:

            def __call__(self, str):
                return f"{self.fg}{str}{self.reset}"

        else:

            def __call__(self, str):
                return str

    blue = Color(12)
    magenta = Color(13)


_LAST_TIME = None


def skip_comments(el: etree._Element) -> Iterator[etree._Element]:
    return (child for child in el if child.tag is not etree.Comment)


def flat_map(fn, iterable):
    return chain.from_iterable(map(fn, iterable))


def logtime(message):
    global _LAST_TIME

    if _LAST_TIME is None:
        d = 0.0
    else:
        d = (monotonic_ns() - _LAST_TIME) / 1e6

    prefix = ansi.blue(f"{d: 8.3f}ms")
    print(f"{prefix} » {message}", file=sys.stderr)

    _LAST_TIME = monotonic_ns()


Identifier = NewType("Identifier", str)

Money: TypeAlias = Literal["$"]

MONEY: Money = "$"

RequiredSkill: TypeAlias = dict[Identifier, float]

IDENTIFIER_PATTERN = re.compile(r"[a-z0-9\._]+", flags=re.IGNORECASE)


def make_identifier(value: str) -> Identifier:
    value = value.strip()

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
    if value == "true":
        return True
    elif value == "false":
        return False
    else:
        raise ValueError(value)


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
class NeedsVariantOf(object):
    identifier: Identifier
    variantof: Identifier


@dataclass
class Tagged(object):
    identifier: Identifier
    tags: list[Identifier]


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
            log_warning(item.message, **item.kwargs)
        else:
            yield item


def log_warning(message, **kwargs):
    print(ansi.magenta(message), file=sys.stderr)

    for key, value in kwargs.items():
        prefix = f"\t» {ansi.blue(key)} "
        value = format_log_value(value, path)

        if "\n" in value:
            print(f"{prefix}...\n{value}", file=sys.stderr)
        else:
            print(f"{prefix}{value}", file=sys.stderr)


def format_log_value(v, path):
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
        return v


def _dedent_strings(strings):
    if not strings:
        return []
    indent = min(re.match(" *", s).end() for s in strings)
    return [s[indent:] for s in strings]


def serialize_dataclass(value):
    if is_dataclass(value):
        return trim_asdict(value)
    else:
        raise TypeError(value)


def trim_asdict(value):
    return {
        field.name: getattr(value, field.name)
        for field in fields(value)
        if field.default != getattr(value, field.name)
    }


@dataclass
class Sprite(object):
    texture: str
    ltwh: tuple[int, int, int, int]


@dataclass
class BaroItem(object):
    element: etree._Element
    identifier: Identifier
    nameidentifier: str | None
    variant_of: Identifier | None
    tags: list[Identifier]

    @property
    def is_variant(self) -> bool:
        return self.variant_of is not None

    def used_in(self, process: Process):
        return (
            any(
                self.identifier == part.what or part.what in self.tags
                for part in process.iter_parts()
            )
            or self.identifier in process.stations
        )


def index_document(doc) -> Iterator[BaroItem | Warning]:
    root = doc.getroot()
    element_tag = root.tag.lower()

    if element_tag == "item":
        items = [root]

    elif element_tag == "items":
        items = root

    else:
        return

    for item in items:

        # skip items with no identifier, we assume they contain no interesting
        # information
        if not item.get("identifier"):
            if item.xpath("Fabricate or Deconstruct or Price"):
                yield Warning(
                    "element has no identifier but has something we care about",
                    element=item,
                )
            continue

        # there are a lot of attributes on these elements,
        # we don't care to explicitly ignore them so don't
        # yield from attrs.warnings()
        attrs = Attribs.from_element(item)

        # FIXME TODO the fields in might not be accurate if variantof is present ...
        try:
            yield BaroItem(
                element=item,
                identifier=attrs.use("identifier", convert=make_identifier),
                nameidentifier=attrs.or_none("nameidentifier"),
                tags=attrs.use("tags", convert=split_identifier_list, default=[]),
                variant_of=attrs.or_none("variantof", convert=make_identifier)
                or attrs.or_none("inherit", convert=make_identifier),
            )
        except Error as err:
            yield err.as_warning()


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

        yield Sprite(texture=attrs.use("texture"), ltwh=ltwh)
    except Error as err:
        yield err.as_warning()


def extract_Item(item) -> Iterator[Process | Warning]:
    for el in skip_comments(item):
        tag = el.tag.lower()

        try:
            if tag == "fabricate":
                yield from extract_Fabricate(el)

            elif tag == "deconstruct":
                yield from extract_Deconstruct(el)

            elif tag == "price":
                yield from extract_Price(el)

        except Error as err:
            yield err.as_warning()


def extract_item_identifier(el) -> Identifier:
    identifier = el.get("identifier")

    if not identifier:
        raise MissingAttribute(attribute="identifier", element=el)

    return make_identifier(identifier)


def extract_Fabricate(
    el,
) -> Iterator[Process | Warning]:
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


def extract_Fabricate_Item(
    el,
) -> Iterator[RequiredSkill | Part | Warning]:
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


def extract_Deconstruct(el) -> Iterator[Process | Warning]:
    attrs = Attribs.from_element(el)

    fab = Process(
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


def extract_Price(el) -> Iterator[Process | Warning]:
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

    is_sold_by_stores_generally = attrs.use("sold", convert=xmlbool, default=True)

    yield from attrs.warnings()

    price = Process(
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
            stations = attrs.use("storeidentifier", convert=split_identifier_list)

            if attrs.use("sold", convert=xmlbool, default=is_sold_by_stores_generally):
                price.stations.extend(stations)

        except Error as err:
            yield err.as_warning()

        yield from attrs.warnings()

    if price.stations:
        yield price


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

    see BarotraumaShared/SharedSource/Prefabs/IImplementsVariants.cs
    """
    applied = etree.Element(variant.tag)
    applied.sourceline = variant.sourceline  # TODO kind of a lie ?
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
                if variant_child.attrib or len(variant_child):
                    applied.append(apply_variant(base_child, variant_child))

                break

        else:
            applied.append(copy(base_child))

    applied.extend(c for c in variant_children if c is not None)

    return applied


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

    def raise_if_not_empty(self):
        if self:
            raise UnexpectedElement(self)

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


def rglobpaths(paths: list[str], glob: str):
    for path in map(Path, paths):
        if path.is_file():
            yield path
        else:
            yield from path.rglob(glob)


_path_cache: dict[str, dict[str, Path]] = {}


def find_texture_path_on_fs(content: Path, texture: str) -> Path | None:
    """content should be a path to barotrauma's Content directory"""
    # - this pretty much has to behave case-insensitive ...
    # - texture paths may be relative to the directory containing barotrauma's
    #   Content directory, so drop that prefix if we find it

    # TODO do something about preventing symlinks or paths leaving the
    # args.content root via .. or whatever

    # fmt: off
    suffix = Path(texture).suffix
    texture = texture.lower()
    texture = drop_prefix(texture, "content/") \
           or drop_prefix(texture, "content\\") \
           or texture
    # fmt: on

    haystack = _path_cache.get(suffix)
    if haystack is None:
        haystack = _path_cache[suffix] = {
            str(p).lower(): p for p in content.rglob(f"*{suffix}")
        }

    for nocasepath, fspath in haystack.items():
        if nocasepath.endswith(texture):
            return content / fspath

    return None


def ltwh_to_ltbr(ltwh: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    (l, t, w, h) = ltwh
    return (l, t, l + w, t + h)


_sprite_cache: dict[Path, "PIL.Image.Image"] = {}


def load_sprite_from_file(
    path: Path, ltwh: tuple[int, int, int, int]
) -> "PIL.Image.Image":
    from PIL import Image

    image = _sprite_cache.get(path)
    if image is None:
        image = _sprite_cache[path] = Image.open(path)

    image = image.crop(ltwh_to_ltbr(ltwh))  # crop to sprite in sheet
    image = image.crop(image.getbbox())  # crop transparency
    image = image.copy()  # thumbnail() is in-place returns None, copy first
    image.thumbnail((48, 48))
    return image


def to_base64(image: "PIL.Image.Image", format="webp") -> str:
    buf = io.BytesIO()
    image.save(buf, format=format)
    return b64encode(buf.getvalue()).decode()


def load_xml_rglob(paths: list[str], glob: str):
    for path in rglobpaths(paths, glob):
        try:
            with path.open() as file:
                doc = etree.parse(file)
        except OSError as err:
            print("uh oh %s: %s", path, err, file=sys.stderr)
            continue
        else:
            yield path, doc


if __name__ == "__main__":
    from argparse import ArgumentParser

    # fmt: off
    parser = ArgumentParser()
    parser.add_argument("-n", "--dry-run", action="store_true", default=False)
    parser.add_argument("--items", nargs="+", help="paths to find item xml data (Content/Items Content/Map)")
    parser.add_argument("--texts", nargs="+", help="paths to find localization xml data (Content/Texts)")
    parser.add_argument("--sprites", nargs="*", type=Path, help="search path for sprites (Content)")
    parser.add_argument("write_sprites", nargs="?", type=Path, help="path to write sprite sheet .css")
    # parser.add_argument("output", nargs='?', default='-', type=Path, help="path to write .json")
    # fmt: on

    args = parser.parse_args()

    # load every xml file and build an index

    logtime("building index")

    index: dict[Identifier, BaroItem] = {}

    for path, doc in load_xml_rglob(args.items, "*.xml"):
        for item in log_warnings(index_document(doc), path=path):
            index[item.identifier] = item

    logtime("loading variants")

    # remove variations where we don't know about the variant_of?
    for item in list(index.values()):
        if item.is_variant and item.variant_of not in index:
            log_warning(
                ansi.magenta("item variant_of not in index"),
                element=item.element,
                variant_of=item.variant_of,
            )
            del index[item.identifier]

    graph = {
        identifier: {item.variant_of}
        for identifier, item in index.items()
        if item.variant_of is not None
    }
    for identifier in TopologicalSorter(graph).static_order():
        if identifier is None:
            # don't how this happens but mypy thinks it can so whatever
            continue

        item = index[identifier]

        if not item.is_variant:
            continue

        variant_of = index[item.variant_of]

        item.element = apply_variant(
            variant_of.element,
            item.element,
            only_tags=("fabricate", "deconstruct", "price", "inventoryicon", "sprite"),
        )

    logtime("reading processes from items")

    processes: list[Process] = []

    for item in index.values():
        processes.extend(log_warnings(extract_Item(item.element)))

    logtime(f"tidying/abbreviating")

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

    logtime("orphaning orphans")

    _unpruned_len = len(index)
    index = {
        k: item
        for k, item in index.items()
        if any(item.used_in(process) for process in processes)
    }

    logtime(f"{len(processes)} Process for {len(index)} (was {_unpruned_len}) items")

    if args.write_sprites and args.sprites:

        logtime("finding sprites")

        sprites: dict[Identifier, Sprite] = {}

        for item in index.values():
            els = item.element.xpath("InventoryIcon") + item.element.xpath("Sprite")
            for sprite in log_warnings(flat_map(extract_Sprite, els)):
                sprites[item.identifier] = sprite
                break

        logtime("generating sprite css")

        write_sprites = os.devnull if args.dry_run else args.write_sprites

        with open(write_sprites, "w") as file:

            for identifier, sprite in sprites.items():

                for sprite_path in args.sprites:
                    texture_path = find_texture_path_on_fs(sprite_path, sprite.texture)
                    if texture_path is not None:
                        break

                else:
                    log_warning(
                        "texture not found", path=sprite.texture, identifier=identifier
                    )
                    continue

                image = load_sprite_from_file(texture_path, sprite.ltwh)

                print(
                    '[data-sprite="%s"] { background: url("data:image/webp;base64,%s") }'
                    % (identifier, to_base64(image)),
                    file=file,
                )

    logtime("i18n")

    # {language: {identifier: humantext}}
    i18n: dict[str, dict[str, str]] = {}

    should_localize: set[str] = set()
    for process in processes:
        should_localize.update(process.stations)

        for part in process.iter_parts():
            if part.what == MONEY:
                continue
            item = index.get(part.what)
            if item and item.nameidentifier:
                should_localize.add(item.nameidentifier)
            else:
                should_localize.add(part.what)

    for path, doc in load_xml_rglob(args.texts, "*/*.xml"):

        root = doc.getroot()
        language = root.get("language")
        if not language:
            continue

        dictionary = i18n.setdefault(language, {})

        if language not in dictionary:
            language_name = root.get("translatedname")
            if language_name:
                dictionary[language] = language_name

        for child in skip_comments(root):

            if not child.text:
                continue

            tag = child.tag.lower()

            if tag == 'credit':
                dictionary['$'] = child.text
                continue

            # fmt: off
            msg = (   drop_prefix(tag, "entityname.")
                   or drop_prefix(tag, "npctitle.") # merchants
                   or drop_prefix(tag, 'fabricationdescription.')) # munition_core etc
            # fmt: on
            if msg not in should_localize:
                continue

            if (current := dictionary.get(msg)) is not None and current != child.text:
                log_warning(
                    "l10n duplicate",
                    language=language,
                    msg=msg,
                    current=current,
                    update=child.text,
                )

            dictionary[msg] = child.text

    for language, dictionary in i18n.items():
        if not_found := should_localize - set(dictionary.keys()):
            log_warning("l10n not found", language=language, not_found=not_found)

    for lang, dictionary in i18n.items():
        logtime(f"{len(dictionary)} in {lang}")

    if not args.dry_run:
        logtime("dumping json")
        dumpme = {
            "tags_by_identifier": {
                identifier: item.tags for identifier, item in index.items() if item.tags
            },
            "procs": processes,
            "i18n": i18n,
        }
        try:
            json.dump(dumpme, default=serialize_dataclass, fp=sys.stdout)
        except BrokenPipeError:
            pass

    logtime("woo hoo!")
