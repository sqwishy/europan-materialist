import { createContext } from 'solid-js'

export type ize = (_: string) => string
export type Dictionary = Record<string, string>

const noop: ize = _ => _

export const Context = createContext<[ize, ize]>([noop, noop]);

export const izes =
  (dictionary?: Dictionary) =>
    dictionary === undefined 
  ? noop
  : (s: string) => dictionary[s] || s

export const izesToLower =
  (dictionary?: Dictionary) =>
    dictionary === undefined 
  ? noop
  : (s: string) => dictionary[s]?.toLowerCase() || s
