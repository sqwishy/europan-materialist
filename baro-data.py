import sys
import re
import os
import json
import logging
import pathlib
from dataclasses import dataclass, is_dataclass, fields
from lxml import etree
from itertools import count
from operator import itemgetter
from typing import Union, NewType


logger = logging.getLogger(__name__)


class ansi:
    # if not sys.stderr.isatty() or 'NOCOLOR' in os.environ:

    class Color(object):
        """256-color mode https://en.wikipedia.org/wiki/ANSI_escape_code#8-bit"""

        reset = "\x1b[0m"

        def __init__(self, n):
            self.fg = f"\x1b[38;5;{n}m"
            # self.bg = f"\x1b[48;5;{n}m"

        def __call__(self, str):
            return f"{self.fg}{str}{self.reset}"

    dark_black = Color(0)
    dark_red = Color(1)
    dark_green = Color(2)
    dark_yellow = Color(3)
    dark_blue = Color(4)
    dark_magenta = Color(5)
    dark_teal = Color(6)
    grey = Color(7)
    dark_grey = Color(8)
    red = Color(9)
    green = Color(10)
    yellow = Color(11)
    blue = Color(12)
    magenta = Color(13)
    teal = Color(14)
    white = Color(15)


Identifier = NewType("Identifier", str)
Tag = NewType("Tag", str)
Money = NewType("Money", str)


def make_identifier(value: str) -> Identifier:
    if not value:
        raise ValueError(value)
    return ":" + value


def make_tag(value: str) -> Tag:
    if not value:
        raise ValueError(value)
    return "#" + value


def money() -> Money:
    return "$"


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
    amount: float
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
    amount: float


@dataclass
class Process(object):
    """Fabricate / Deconstruct / Price"""

    uses: list[Part | RandomChoices]
    skills: dict[str, int]
    station: str
    time: float
    needs_recipe: bool = False
    description: str = None


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


def warn_missing_attribute(*, element, attribute):
    return Warning(
        "required attribute missing from element", attribute=attribute, element=element
    )


def warn_unexpected_element(*, unexpected):
    return Warning("unexpected element", unexpected=unexpected)


def log_warnings(it, path):
    for item in it:
        if isinstance(item, Warning):

            print(ansi.magenta(item.message), file=sys.stderr)

            for key, value in item.kwargs.items():
                prefix = f"\t» {ansi.blue(key)} "
                # indent = f"\t  {' ' * len(key)} "
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


def extract_document(doc):
    for item in doc.xpath("//Items/*"):
        yield from extract_Item(item)


def extract_Item(item):
    # tags = (item.get('tags') or item.get('Tags') or '').split(',')

    if item.get("variantof") or item.get("inherit"):
        return  # yield Warning("skipping variantof/inherit TODO", element=item)

    for el in item.xpath("Fabricate"):
        try:
            yield from extract_Fabricate(el)
        except MissingAttribute as err:
            yield err.warn()

    for el in item.xpath("Deconstruct"):
        try:
            yield from extract_Deconstruct(el)
        except MissingAttribute as err:
            yield err.warn()

    for el in item.xpath("Price"):
        try:
            yield from extract_Price(el)
        except MissingAttribute as err:
            yield err.warn()


def extract_item_identifier(el):
    identifier = el.get("identifier") or el.get("nameidentifier")

    if not identifier:
        raise MissingAttribute(
            dict(attribute=("identifier", "nameidentifier"), element=el)
        )

    return make_identifier(identifier)


def extract_Fabricate(el):
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
                amount=attrs.use("amount", convert=float, default=1.0),
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

                elif isinstance(item, dict):  # skill ...
                    res.skills.update(item)

                else:
                    yield item
        except MissingAttribute as err:
            yield err.warn()

    yield res


def extract_Fabricate_Item(el):
    attrs = Attribs.from_element(el)

    if el.tag == "RequiredSkill":
        yield {attrs.use("identifier"): attrs.use("level", convert=float)}

    elif el.tag in ("RequiredItem", "Item"):
        attrs.ignore("usecondition", "header", "defaultitem")

        what = attrs.or_none("identifier", convert=make_identifier)
        if what is None:
            what = attrs.or_none("tag", convert=make_tag)
        if what is None:
            raise MissingAttribute(dict(attribute=("identifier", "tag"), element=el))

        # this is an ingredient/required item. it is consumed
        # during fabrication, so the amount is negative
        amount = -(
            attrs.or_none("amount", convert=float)
            or attrs.use("count", convert=float, default=1)
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


def extract_Deconstruct(el):
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


def extract_Deconstruct_Item(el):
    attrs = Attribs.from_element(el)
    attrs.ignore(
        "commonness",
        "copycondition",
        "outconditionmin",
        "outconditionmax",
        "activatebuttontext",
        "infotext",
        "infotextonotheritemmissing",
    )

    if el.tag in (
        "RequiredItem",  # not to be confused with requiredotheritem lulz
        "Item",
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


def extract_Price(el):
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

        if child.tag != "Price":
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


class Attribs(dict):
    @classmethod
    def from_element(cls, element):
        self = cls(element.attrib.iteritems())
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

    def warnings(self):
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

    logging.basicConfig(level=logging.DEBUG)

    # ns = etree.FunctionNamespace(None)

    # @ns
    # def lower(context, a):
    #     print(args)
    #     breakpoint()
    #     pass

    items = []

    for path in rglobpaths(args.paths, "*.xml"):
        try:
            with path.open() as file:
                doc = etree.parse(file)
        except OsError as err:
            logger.warning("%s: %s", path, err)
            continue
        else:
            items.extend(log_warnings(extract_document(doc), path=path))

    if not args.dry_run:
        try:
            json.dump(items, default=serialize_dataclass, fp=sys.stdout)
        except BrokenPipeError:
            pass
