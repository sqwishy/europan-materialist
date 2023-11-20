import * as Data from "./Data"
import * as Locale from "./Locale"

type AmountedFilter = (_: { amount: number }) => boolean
type PartFilter = (_: Data.Part) => boolean
type UsedInFilter = (_: Data.Part | Data.WeightedRandomWithReplacement) => boolean
type IdentifierFilter = (_: Data.Identifier) => boolean

export const sAmount =
  (context : "only-consumed" | "only-produced"): AmountedFilter =>
      context === "only-consumed"
    ? ({ amount }) => amount < 0
    : ({ amount }) => amount > 0


export const sIdentifier =
  ({ substring, localize }: { substring: string, localize?: Locale.ize }): IdentifierFilter =>
    substring === ""
  ? (_) => true
  :    (identifier) => identifier.includes(substring)
    || (!!localize && localize(identifier).includes(substring))


export const sPart =
  ({ identifier, amount }: { identifier: IdentifierFilter, amount?: AmountedFilter }) =>
     amount === undefined
  ? (i: Data.Part) => identifier(i.what)
  : (i: Data.Part) => identifier(i.what) && amount(i)


export const sUsedInProcess =
  ({ part }: { part: PartFilter }) =>
  (i: Data.Part | Data.WeightedRandomWithReplacement): boolean =>
      "what" in i
    ? part(i)
    : i.weighted_random_with_replacement.some(part)


export const sProcesses =
  ({ identifier, amount, usedIn }:
   { identifier: IdentifierFilter, amount?: AmountedFilter, usedIn: UsedInFilter }) =>
    amount === undefined
  ? (p: Data.Process): boolean => p.uses.some(usedIn)
                               || p.stations.some(identifier)
  : (p: Data.Process): boolean => p.uses.some(usedIn)


export const sEntities =
  ({ amount, identifier }: { identifier: IdentifierFilter, amount?: AmountedFilter }) =>
    !amount
  ? ([ i, tags ]: [ Data.Identifier, Data.Identifier[] ]) =>
       identifier(i) || tags.some(identifier)
  : (_: any) => false
