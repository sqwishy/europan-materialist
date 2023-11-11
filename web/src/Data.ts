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
  uses: (Part | WeightedRandomWithReplacement)[],
  skills: Record<Identifier, number>,
  stations: Identifier[],
  time: number
  needs_recipe?: boolean
  description?: string
}

export type Stuff = {
  tags_by_identifier: Record<Identifier, Identifier[]>,
  procs: Process[],
  i18n: Record<string, Record<string, string>>,
}

export async function fetchStuff(): Promise<Stuff> {
  const res = await fetch(`${import.meta.env.BASE_URL}stuff.json`)
  if (!res.ok)
    throw new Error(res.statusText)
  return await res.json();
}
