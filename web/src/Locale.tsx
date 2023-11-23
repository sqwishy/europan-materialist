import { createContext } from 'solid-js'

export type ize = (_: string) => string
export type Dictionary = Record<string, string>

export const Context = createContext<[ize, ize]>([_ => _, _ => _]);

export const izes =
  (dictionary: () => Dictionary | undefined) =>
  (s: string) => dictionary()?.[s] || s

export const izesToLower =
  (dictionary: () => Dictionary | undefined) =>
  (s: string) => dictionary()?.[s]?.toLowerCase() || s
