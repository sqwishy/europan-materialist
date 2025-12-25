import { createSignal, createResource, createEffect,
         getOwner, runWithOwner, untrack } from "solid-js";
import type { Resource } from "solid-js";

// import * as Remote from "./Remote";


export type AsyncResource<T> = {
	loaded: () => T | null,
	isLoading: () => boolean,
	last: () => T | null,
	error: () => any,
	hasError: () => boolean,
	insert: (_: T) => T,
	// cancels any promise in-progress
	fetch: <F extends T,>(_: Promise<F>) => Promise<F>,
	fetchIn: <F extends T,>(_: () => Promise<F>, ms: number) => void,
	// uses factory if possible, will not cancel a promise in-progress
	refetch: () => Promise<T>,
	// promise: () => Promise<T>,
}

type CreateAsyncOptions<T> =
	{ tracks?: () => boolean,
    factory?: () => Promise<T> }


const empty =
	() =>
	new Promise<null>(ok => ok(null))


export const createAsyncLazy =
	<T,>(o?: CreateAsyncOptions<T | null>): AsyncResource<T | null> =>
	createAsync<T | null>(new Promise<null>(ok => ok(null)), o)


/* another failed attempt at wrangling createResource in a way to produce readable code.
 * instead i am left with more garbage upon garbage. SolidJS fucking sucks with async. */
export const createAsync =
	<T,>(pinit: Promise<T>, o?: CreateAsyncOptions<T>): AsyncResource<T> =>
	{

		let generation = 0;

		const wrap =
			<P,>(p: Promise<P>): Promise<P> => {
				const us = ++generation
				return p.then(ok => {
					/* TODO can/should we cancel a fetch() here?
					 * using the abort signal or whatever? */
					if (us != generation) throw new Error("cancelled")
					else return ok
				})
			}

		const [get, set] = createSignal<Promise<T>>(wrap(pinit))
		/* this messes up my syntax highlighting lmao fuck computers */
		// const set =
		// 	<P extends T,>(p: Promise<P>): Promise<P> =>
		// 	set_(wrap(p))
		const [r] = createResource(get, p => p)

		const tracks = o?.tracks
		if (tracks)
			/* FIXME this calls once in the beginning for no good reason,
			 * maybe use createReaction if you can get that to re-run reliably */
			createEffect(() => {
				if (tracks())
					/* I don't 100% know if re-using this promise will work out how I want,
					 * good luck to me I guess lmao */
					set(wrap(pinit))
			})

		return {
			loaded: () => loadedResource(r),
			isLoading: () => r.loading,
			last: () => latestResource(r),
			error: () => r.error,
			hasError: () => Boolean(r.error),
			insert: (t: T) => (set(wrap(new Promise<T>(ok => ok(t)))), t),
			fetch: (p) => set(wrap(p)),
			fetchIn: (pf, ms) => {
				const owner = getOwner()
				const later = () => runWithOwner(owner, () => set(wrap(pf())))
				setTimeout(later, ms)
			},
			refetch: () => untrack(() => r.loading ? get() : null)
			            || set(wrap(o?.factory?.() || pinit)),
		}
	}


export const loadedResource = <T,>(r: Resource<T>): T | null => {
	let result;
	if (   !r.loading
	    && !r.error
	    && (undefined !== (result = r())))
		return result
	else
		return null
}


export const latestResource = <T,>(r: Resource<T>): T | null => {
	let result;
	if (   !r.error
	    && (undefined !== (result = r.latest)))
		return result
	else
		return null
}
