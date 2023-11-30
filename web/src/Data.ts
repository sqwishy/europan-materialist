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
  id: string,
  uses: (Part | WeightedRandomWithReplacement)[],
  skills: Record<Identifier, number>,
  stations: Identifier[],
  time: number
  needs_recipe?: boolean
  description?: string
}

export type Stuff = {
  name: string,
  version: string,
  tags_by_identifier: Record<Identifier, Identifier[]>,
  processes: Process[],
  i18n: Record<string, Record<string, string>>,
}

export async function fetchStuff(url: string): Promise<Stuff> {
  const res = await fetch(url)
  if (!res.ok)
    throw new Error(res.statusText)
  return await res.json();
}
