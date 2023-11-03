import json
import os
import pathlib
import re
import sys
from copy import copy
from dataclasses import dataclass, is_dataclass, fields
from graphlib import TopologicalSorter
from itertools import count
from lxml import etree
from operator import itemgetter
from typing import Union, NewType, TypeAlias, Callable, Generator, overload, TypeVar
from time import monotonic_ns


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


def skip_comments(el: etree._Element) -> Generator[etree._Element, None, None]:
    return (child for child in el if child.tag is not etree.Comment)


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

Tag = NewType("Tag", str)

Money = NewType("Money", str)

RequiredSkill: TypeAlias = dict[Identifier, float]


def make_identifier(value: str) -> Identifier:
    if not value:
        raise ValueError(value)
    return Identifier(":" + value)


def make_tag(value: str) -> Tag:
    if not value:
        raise ValueError(value)
    return Tag("#" + value)


def split_tag_list(value: str) -> list[Tag]:
    return [make_tag(s) for s in value.split(",") if s]


def money() -> Money:
    return Money("$")


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

    what: Union[Identifier, Tag, Money]
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


@dataclass
class RandomChoices(object):
    """barotrauma seems to do weighted random with replacement ..."""

    weighted_random_with_replacement: list[Part]
    amount: int


@dataclass
class Process(object):
    """Fabricate / Deconstruct / Price"""

    uses: list[Part | RandomChoices]
    skills: dict[Identifier, float]
    station: str
    time: float
    needs_recipe: bool = False
    description: str | None = None


class MissingAttribute(ValueError):
    def warn(self):
        (args,) = self.args
        return warn_missing_attribute(**args)


# def warn_on_error(it):
#     try:
#     except MissingAttribute as err:
#         yield err.warn()


class Warning(object):
    def __init__(self, message="", **kwargs):
        self.message = message
        self.kwargs = kwargs


@dataclass
class NeedsVariantOf(object):
    identifier: Identifier
    variantof: Identifier


@dataclass
class Tagged(object):
    identifier: Identifier
    tags: list[Tag]


def warn_missing_attribute(*, element: etree._Element, attribute):
    return Warning(
        "required attribute missing from element", attribute=attribute, element=element
    )


def warn_unexpected_element(*, unexpected):
    return Warning("unexpected element", unexpected=unexpected)


def log_warnings(it, *, path=None):
    for item in it:
        if isinstance(item, Warning):

            print(ansi.magenta(item.message), file=sys.stderr)

            for key, value in item.kwargs.items():
                prefix = f"\t» {ansi.blue(key)} "
                value = format_log_value(value, path)

                if "\n" in value:
                    print(f"{prefix}...\n{value}", file=sys.stderr)
                else:
                    print(f"{prefix}{value}", file=sys.stderr)

        else:
            yield item


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
class BaroItem(object):
    element: etree._Element
    identifier: Identifier
    variant_of: Identifier | None
    tags: list[Tag]

    @property
    def is_variant(self):
        return self.variant_of is not None


def index_document(doc) -> Generator[BaroItem | Warning, None, None]:
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

        yield BaroItem(
            element=item,
            identifier=attrs.use("identifier", convert=make_identifier),
            tags=attrs.use("tags", convert=split_tag_list, default=[]),
            variant_of=attrs.or_none("variantof", convert=make_identifier)
            or attrs.or_none("inherit", convert=make_identifier),
        )


def extract_Item(
    item,
) -> Generator[Process | Warning, None, None]:
    for el in skip_comments(item):
        tag = el.tag.lower()

        try:
            if tag == 'fabricate':
                yield from extract_Fabricate(el)

            elif tag == 'deconstruct':
                yield from extract_Deconstruct(el)

            elif tag == 'price':
                yield from extract_Price(el)
        except MissingAttribute as err:
            yield err.warn()


def extract_item_identifier(el) -> Identifier:
    # TODO nameidentifier is used for display, should probably be used no this way
    identifier = el.get("identifier")  # or el.get("nameidentifier")

    if not identifier:
        raise MissingAttribute(dict(attribute="identifier", element=el))

    return make_identifier(identifier)


def extract_Fabricate(
    el,
) -> Generator[Process | Warning, None, None]:
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
        station=attrs.use("suitablefabricators"),
        time=attrs.use("requiredtime", convert=float, default=1.0),
        needs_recipe=attrs.use("requiresrecipe", default=False, convert=xmlbool),
        description=attrs.opt("displayname"),
    )

    requiredmoney = attrs.opt("requiredmoney")
    if requiredmoney:
        res.uses.append(Part(what=money(), amount=requiredmoney))

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
        except MissingAttribute as err:
            yield err.warn()

    yield res


def extract_Fabricate_Item(
    el,
) -> Generator[RequiredSkill | Part | Warning, None, None]:
    attrs = Attribs.from_element(el)

    if el.tag.lower() == "requiredskill":
        skill_identifier = attrs.use("identifier", convert=make_identifier)
        skill_level = attrs.use("level", convert=float)
        yield {skill_identifier: skill_level}

    elif el.tag.lower() in ("requireditem", "item"):
        attrs.ignore("usecondition", "header", "defaultitem")

        what = attrs.or_none("identifier", convert=make_identifier)
        if what is None:
            what = attrs.or_none("tag", convert=make_tag)
        if what is None:
            raise MissingAttribute(dict(attribute=("identifier", "tag"), element=el))

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


def extract_Deconstruct(el) -> Generator[Process | Warning, None, None]:
    attrs = Attribs.from_element(el)

    fab = Process(
        uses=[
            Part(
                what=extract_item_identifier(el.getparent()),
                amount=-1,
                # name=attrs.opt("displayname"),
            )
        ],
        skills={},
        time=attrs.use("time", convert=float, default=1.0),
        station=attrs.use("requireddeconstructor", default="deconstructor"),
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
        except MissingAttribute as err:
            yield err.warn()

    yield fab

    yield from attrs.warnings()


def extract_Deconstruct_Item(el) -> Generator[Part | Warning, None, None]:
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


def extract_Price(el) -> Generator[Process | Warning, None, None]:
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

    is_sold_by_store_generally = attrs.use("sold", convert=xmlbool, default=True)

    yield from attrs.warnings()

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

        station = attrs.use("storeidentifier")

        if attrs.use("sold", convert=xmlbool, default=is_sold_by_store_generally):
            yield Process(
                station=station,
                uses=[Part(what=money(), amount=-1)],
                skills={},
                time=0,
            )

        yield from attrs.warnings()


def apply_variant(
    base: etree._Element, variant: etree._Element, only_tags=()
) -> etree._Element:
    """Given <variant variantof=base>, apply variant over top of base, returning a new element.

    variantof is some sort of "inheritance" trash where some element can be a
    variant of another and that element's definition is merged over that of the
    thing it's a variant of.

    mostly this copies the referenced element and then adds or replaces
    existing attributes from the variant; working recursively by pairing child
    elements by their tag name

    some uh ... "features":

    - some element attribute values are numbers prefixed with * or +, those are
      added or multiplied with the existing value i guess but i don't think
      this feature is used? so I'm ignoring it

    - if an element in a variant has no children or attributes, it removes the
      element instead of merging???

    see BarotraumaShared/SharedSource/Prefabs/IImplementsVariants.cs
    """
    applied = etree.Element(variant.tag)
    applied.sourceline = variant.sourceline  # TODO kind of a lie ?
    # mypy upset that we're passing a generator???
    applied.attrib.update((k.lower(), v) for k, v in base.attrib.iteritems())  # type: ignore
    applied.attrib.update((k.lower(), v) for k, v in variant.attrib.iteritems())  # type: ignore
    # lxml typings are really on strong stuff
    applied.attrib.pop("variantof", None)  # type: ignore
    applied.attrib.pop("inherit", None)  # type: ignore

    variant_children: list[etree._Element | None] = list(skip_comments(variant))

    # seems to be a funny special case where <Clear/> is used to produce an
    # element with no children from either the base or the variant
    if any(c.tag.lower() == "clear" for c in variant_children):  # type: ignore
        return applied

    for base_child in skip_comments(base):

        child_tag = base_child.tag.lower()

        # optimization, allow ault
        if only_tags and child_tag not in only_tags:
            continue

        for i, variant_child in enumerate(variant_children):
            if variant_child is not None and child_tag == variant_child.tag.lower():
                variant_children[i] = None

                # if the variant element has no attributes or children, this
                # "removes" the element instead of merging it with the include
                # the pair from the base element
                if variant_child.attrib or len(variant_child):
                    applied.append(apply_variant(base_child, variant_child))

                break

        else:
            applied.append(copy(base_child))

    applied.extend(c for c in variant_children if c is not None)

    return applied


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
                raise MissingAttribute(
                    dict(attribute=attribute, element=self.__element)
                )

            else:
                return default

        if convert is not None:
            value = convert(value)

        return value

    def opt(self, attribute, *, convert=None):
        return self.use(attribute, convert=convert, default=None)

    or_none = opt

    def raise_if_not_empty(self):
        if self:
            raise UnexpectedElement(self)

    def warnings(self) -> Generator[Warning, None, None]:
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


def rglobpaths(paths, glob):
    for path in map(pathlib.Path, args.paths):
        if path.is_file():
            yield path
        else:
            yield from path.rglob(glob)


if __name__ == "__main__":
    from argparse import ArgumentParser

    parser = ArgumentParser()
    parser.add_argument("paths")
    parser.add_argument("-n", "--dry-run", action="store_true", default=False)

    args = parser.parse_args()

    # load every xml file and build an index

    logtime("building index")

    index: dict[Identifier, BaroItem] = {}

    for path in rglobpaths(args.paths, "*.xml"):
        try:
            with path.open() as file:
                doc = etree.parse(file)
        except OSError as err:
            print("uh oh %s: %s", path, err, file=sys.stderr)
            continue
        else:
            for item in log_warnings(index_document(doc), path=path):
                index[item.identifier] = item

    # TODO check variations where we don't know about the variant_of?

    logtime("loading variants")

    graph = {
        item.identifier: {item.variant_of} for item in index.values() if item.is_variant
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
            only_tags=("fabricate", "deconstruct", "price"),
        )

    logtime("reading information from items")

    processes: list[Process] = []

    for item in index.values():
        processes.extend(log_warnings(extract_Item(item.element)))

    logtime(f"found {len(processes)} Process")

    if not args.dry_run:
        logtime("dumping json")
        try:
            json.dump([], default=serialize_dataclass, fp=sys.stdout)
        except BrokenPipeError:
            pass

    logtime("woo hoo!")
