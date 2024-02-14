import * as Game from '../assets/bundles'
import * as Locale from "./Locale"

type AmountedFilter = (_: { amount: number }) => boolean
type PartFilter = (_: Game.Part) => boolean
type UsedInFilter = (_: Game.Part | Game.WeightedRandomWithReplacement) => boolean
type IdentifierFilter = (_: Game.Identifier) => boolean
type EntityFilter = (_: Game.Entity) => boolean

export const either =
  <T,>(a: (_: T) => boolean, b: (_: T) => boolean) =>
  (t: T) => a(t) || b(t)


export const both =
  <T,>(a: (_: T) => boolean, b: (_: T) => boolean) =>
  (t: T) => a(t) && b(t)


export const all =
  <T,>(ff: ((_: T) => boolean)[]) =>
  (t: T) => ff.every((f: (_: T) => boolean) => f(t))


export function memo<K, V>(inner: (_: K) => V) {
  const map = new Map();
  return function(key: K): V {
    let value = map.get(key);
    if (value === undefined)
      map.set(key, (value = inner(key)))
    return value
  }
}

export const amount =
  (context : "only-consumed" | "only-produced"): AmountedFilter =>
      context === "only-consumed"
    ? ({ amount }) => amount < 0
    : ({ amount }) => amount > 0


export const containsIdentifier =
  ({ text, localize }: { text: string, localize?: Locale.ize }): IdentifierFilter =>
    text === ""
  ? (_) => true
  :    (identifier) => identifier.includes(text)
    || (!!localize && localize(identifier).includes(text))


export const exactIdentifier =
  ({ text, localize }: { text: string, localize?: Locale.ize }): IdentifierFilter =>
    text === ""
  ? (_) => true
  :    (identifier) => identifier === text
    || (!!localize && localize(identifier) === text)


// this looks so stupid, this language is so silly
  export function entityToIdentifierFilter(
    { bundle, entity } : { bundle: Game.Bundle, entity: EntityFilter }
  ): IdentifierFilter
{
  const map = new Map(bundle.entities.map((e) => [e.identifier, e]))
  return function(i: Game.Identifier) {
    let e;
    return (undefined != (e = map.get(i))) && entity(e)
  }
}


export const part =
  ({ identifier, amount }: { identifier: IdentifierFilter, amount?: AmountedFilter }): PartFilter =>
     amount === undefined
  ? (i: Game.Part) => identifier(i.what)
  : (i: Game.Part) => identifier(i.what) && amount(i)


export const usedInProcess =
  ({ part }: { part: PartFilter }): UsedInFilter =>
  (i: Game.Part | Game.WeightedRandomWithReplacement): boolean =>
      "what" in i
    ? part(i)
    : i.weighted_random_with_replacement.some(part)


export const processes =
  ({ identifier, amount, usedIn }:
   { identifier: IdentifierFilter, amount?: AmountedFilter, usedIn: UsedInFilter }) =>
    amount === undefined
  ? (p: Game.Process): boolean => p.uses.some(usedIn)
                               || p.stations.some(identifier)
  : (p: Game.Process): boolean => p.uses.some(usedIn)


export const entities =
  ({ identifier }: { identifier: IdentifierFilter }): EntityFilter =>
  (entity: Game.Entity) =>
     identifier(entity.identifier)
  || entity.tags.some(identifier)
  || Boolean(entity.package && identifier(entity.package))
