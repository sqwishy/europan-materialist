export const last =
	<T,>(array: T[]) => array[array.length - 1];

export const appends =
	<T,>(...item: T[]) =>
	<U,>(array: (T | U)[]) =>
		[...array, ...item];

export const removesAt =
	(...indexes: number[] /* in ascending order */) =>
	<T,>(array: T[]) =>
		[0, ...indexes.map((n) => n + 1)].flatMap((n, i) =>
			array.slice(n, indexes[i])
		);

export const getsAt =
	(...indexes: number[]) =>
	<T,>(array: T[]) =>
		indexes.map((i) => array[i]);

export const insertsAt =
	(index: number) =>
	<T,>(insert: T[]) =>
	(array: T[]) =>
		index < array.length
			? [...array.slice(0, index), ...insert, ...array.slice(index)]
			: [...array, ...insert];

export const adds =
	(r: number) =>
	(l: number) =>
	l + r

export const ignoresFirstCall =
	<R,>(f: (..._: any[]) => R, isFirst = true) =>
	(...a: any[]) => isFirst ? (isFirst = false, undefined) : f(...a)

export const state =
	<T,>(i: T) =>
	(n?: T) => (n === undefined ? i : i = n)

export const clamp =
	(v: number, lo: number, hi: number) =>
	Math.min(Math.max(lo, v), hi)

export const clamps =
	(lo: number, hi: number) =>
	(v: number) =>
	clamp(v, lo, hi)

export const unitToRad = (h: number): number => h * 2 * Math.PI

export const radToUnit = (h: number): number => h / (2 * Math.PI)

export const unreachable = (_: never) => {}

export const dbg =
	<T,>(v: T): T => (console.log(v), v)

export const assert =
	<T,>(v: T): T => {
		if (!v) throw Error(`${v}`)
		else return v;
	}

export const zzzMs =
	(n: number) => new Promise(ok => setTimeout(ok, n))

export const nanToZero =
	(v: number) => Number.isNaN(v) ? 0 : v

export const parseNumber =
	(v: string | number | undefined) =>
	Number.isFinite(v = Number(v)) ? v : 0

export const bezier_linear =
	(t: number, a: number, b: number) =>
	a + (b - a) * t

export const lerp = bezier_linear

export const bezier =
	(t: number, ...p: number[]): number => {
		switch (p.length) {
			case 0: return t
			case 1: return p[0]
			case 2: return bezier_linear(t, p[0], p[1])
			default: return bezier(t, ...pairwise(p).map(([a, b]) => bezier_linear(t, a, b)))
		}
	}
	
export const pairwise =
	<T>(a: T[]) =>
	a.slice(0, -1).map((v, i) => [v, a[i + 1]])

export const cycles =
	<T>(...items: [T, ...T[]]): (() => T) => {
		let i = 0
		return () => i < items.length ? items[i++] : items[(i = 1, 0)]
	}
