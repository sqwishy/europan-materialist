import { createResource } from "solid-js";
import type { ResourceReturn, ResourceActions } from "solid-js";

import { z } from "zod"

import * as F from "./F";


export type ResponseDetails = { code: number, status: string, body: string }


export const API = import.meta.env.VITE_API_URL || "http://10.0.69.3:8848"


export type Resource<T> = {
	// Object.defineProperties makes typescript shit itself?
	// (): () => T | null,
	loaded: () => T | null,
	hasLoaded: () => boolean,
	isLoading: () => boolean,
	last: () => T | null,
	error: () => any,
	hasError: () => boolean,
} & ResourceActions<T | undefined>


export const wrapResource =
	<T,>([r, { mutate, refetch }]: ResourceReturn<T>): Resource<T> =>
	({
		loaded: () => loadedResource(r),
		hasLoaded: () => Boolean(r.loading),
		isLoading: () => r.loading,
		last: () => latestResource(r),
		error: () => r.error,
		hasError: () => Boolean(r.error),
		mutate,
		refetch,
	})


export type RemoteOpts = { lazy?: boolean }


export type Remote<P, T> = {
	_map: Map<P, Resource<T>>,
	resource: (_: P, opts?: RemoteOpts) => Resource<T>,
}


export type Remotes = {
	getWorkshopItemVersions: (_: string, o?: RemoteOpts) => Resource<WorkshopItemList>,
	refreshWorkshopItem: (_: string, o?: RemoteOpts) => Resource<WorkshopItem | WorkshopCollection>,
	downloadVersion: (_: number, o?: RemoteOpts) => Resource<WorkshopItem>,
	getBuild: (_: number | string, o?: RemoteOpts) => Resource<BuildResult>,
	waitOnPublish: (_: number, o?: RemoteOpts) => Resource<PublishResult>,
	// submitBuild: (_: SubmitBuild, o?: RemoteOpts) => Resource<BuildResult>,
}


export const createRemote =
	<P, R,>(f: (_: P) => Promise<R>): Remote<P, R> =>
	{
		let self: Remote<P, R>;
		return self = {
			_map: new Map(),
			resource: (i: P, o?: RemoteOpts) => {
					let v
					if ((v = self._map.get(i)) == undefined) {
						/* FIXME mixing lazy and not lazy with the same key will to be wacky */
						if (o?.lazy)
							// v = wrapResource(createResource(F.ignoresFirstCall(() => i), f))
							v = wrapResource(createResource(i, F.ignoresFirstCall(f) as (_: P) => Promise<R>))
						else
							v = wrapResource(createResource(i, f))
						self._map.set(i, v)
					}
					return v
			},
		}
	}


export const createRemotes = (): Remotes => ({
	getWorkshopItemVersions: createRemote(requestGetWorkshopItemList).resource,
	refreshWorkshopItem: createRemote(requestRefreshItem).resource,
	downloadVersion: createRemote(requestDownloadVersion).resource,
	getBuild: createRemote(requestGetBuild).resource,
	waitOnPublish: createRemote(requestWaitOnPublish).resource,
	// submitBuild: createRemote(requestSubmitBuild).resource,
})


export const workshopUrl = (workshopid: string) => `https://steamcommunity.com/sharedfiles/filedetails/?id=${workshopid}`


export const errForResponse = async (r: Response) => {
	let cause: ResponseDetails = {
		code: r.status,
		status: r.statusText,
		body: await r.text(),
	}
	return new Error(r.statusText, { cause })
}


export const loadedResource = <T,>(r: ResourceReturn<T>[0]): T | null => {
	let result;
	if (   !r.loading
	    && !r.error
	    && (undefined !== (result = r())))
		return result
	else
		return null
}


export const latestResource = <T,>(r: ResourceReturn<T>[0]): T | null => {
	let result;
	if (   !r.error
	    && (undefined !== (result = r.latest)))
		return result
	else
		return null
}


/* POST /workshop-item/:pk/ */
export const requestGetWorkshopItemList = async (workshopid: string): Promise<WorkshopItemList> => {
	// await F.zzz(1123 + 234 * Math.random())

	const params = new URLSearchParams();
	params.append("workshopid", workshopid);

	const res = await fetch(`${API}/workshop-item/?${params}`)

	if (!res.ok)
		throw await errForResponse(res)

	return WorkshopItemList.parse(await res.json())
}


/* POST /workshop-item/:pk/download/ */
export const requestDownloadVersion = async (pk: number): Promise<WorkshopItem> => {
	const res = await fetch(`${API}/workshop-item/${pk}/download/`,
	                        { method: "POST" })
	if (!res.ok)
		throw await errForResponse(res)

	return WorkshopItem.parse(await res.json())
}


/* POST /workshop-item/ */
export const requestRefreshItem = async (
	workshopid : string
): Promise<WorkshopItem | WorkshopCollection> => {
	let body = JSON.stringify({ workshopid })

	const res = await fetch(`${API}/workshop-item/`,
	                        { method: "POST", body })
	if (!res.ok)
		throw await errForResponse(res)

	/* this endpoint returns 303 if it finds a workshop item,
	 * or 200 (no redirect) for a collection  */
	if (res.redirected)
		return WorkshopItem.parse(await res.json())

	else
		return WorkshopCollection.parse(await res.json())
}


/* GET /build/:pk/ */
export const requestGetBuild = async (pk: number | string): Promise<BuildResult> => {
	const res = await fetch(`${API}/build/${pk}/`)

	if (!res.ok)
		throw await errForResponse(res)

	return BuildResult.parse(await res.json())
}


/* POST /build/ */
export const requestSubmitBuild = async (build: SubmitBuild): Promise<BuildResult> => {
	const res = await fetch(`${API}/build/`,
	                        { method: 'POST', body: JSON.stringify(build) })

	if (!res.ok)
		throw await errForResponse(res)

	return BuildResult.parse(await res.json())
}


export const requestWaitOnPublish = async (pk: number): Promise<PublishResult> => {
	const res = await fetch(`${API}/publish/${pk}/wait/`)

	if (!res.ok)
		throw await errForResponse(res)

	return PublishResult.parse(await res.json())
}


export const requestPing = async (): Promise<string> => {
	const res = await fetch(`${API}/ping/`)

	if (!res.ok)
		throw await errForResponse(res)

	return await res.text()
}


export const File = z.object({
	size: z.number(),
})

export const ContentPackage = z.object({
	name: z.string(),
	version: z.string(),
})

export const WorkshopItem = z.object({
	pk: z.number(),
	workshopid: z.string(),
	title: z.string(),
	authors: z.array(z.string()),
	version: z.number(),
	file: z.nullish(File),
	content: z.nullish(ContentPackage),
})

export const WorkshopItemList = z.array(WorkshopItem)

export const WorkshopCollection = z.object({
	workshopid: z.string(),
	title: z.string(),
	authors: z.array(z.string()),
	collection: z.array(z.string()),
})

export type File = z.infer<typeof File>
export type ContentPackage = z.infer<typeof ContentPackage>
export type WorkshopItem = z.infer<typeof WorkshopItem>
export type WorkshopItemList = z.infer<typeof WorkshopItemList>
export type WorkshopCollection = z.infer<typeof WorkshopCollection>


export const BuildItem = z.object({
	pk: z.number(),
	workshopid: z.string(),
})


export const SubmitBuild = z.object({
	name: z.string(),
	items: z.array(z.number()),
})


export const BuildResult = z.object({
  pk: z.number(),
	name: z.string(),
	exit_code: z.nullish(z.number()),
	output: z.nullish(z.string()),
	items: z.array(BuildItem),
  fragment: z.nullish(z.object({ size: z.number() })),
	published: z.nullish(z.number()),
})

export type BuildItem = z.infer<typeof BuildItem>
export type SubmitBuild = z.infer<typeof SubmitBuild>
export type BuildResult = z.infer<typeof BuildResult>

export const PublishResult = z.object({
	exit_code: z.number(),
	public_url: z.string(),
})

export type PublishResult = z.infer<typeof PublishResult>
