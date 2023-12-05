import * as Game from '../assets/bundles'
import * as Locale from "./Locale"

type AmountedFilter = (_: { amount: number }) => boolean
type PartFilter = (_: Game.Part) => boolean
type UsedInFilter = (_: Game.Part | Game.WeightedRandomWithReplacement) => boolean
type IdentifierFilter = (_: Game.Identifier) => boolean


export const amount =
  (context : "only-consumed" | "only-produced"): AmountedFilter =>
      context === "only-consumed"
    ? ({ amount }) => amount < 0
    : ({ amount }) => amount > 0


export const identifier =
  ({ substring, localize }: { substring: string, localize?: Locale.ize }): IdentifierFilter =>
    substring === ""
  ? (_) => true
  :    (identifier) => identifier.includes(substring)
    || (!!localize && localize(identifier).includes(substring))


export const part =
  ({ identifier, amount }: { identifier: IdentifierFilter, amount?: AmountedFilter }) =>
     amount === undefined
  ? (i: Game.Part) => identifier(i.what)
  : (i: Game.Part) => identifier(i.what) && amount(i)


export const usedInProcess =
  ({ part }: { part: PartFilter }) =>
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
  ({ amount, identifier }: { identifier: IdentifierFilter, amount?: AmountedFilter }) =>
    !amount
  ? ([ i, tags ]: [ Game.Identifier, Game.Identifier[] ]) =>
       identifier(i) || tags.some(identifier)
  : (_: any) => false
