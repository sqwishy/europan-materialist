import { createResource } from "solid-js";
import type { ResourceReturn } from "solid-js";

import { z } from "zod"

import * as F from "./F";
import { AsyncResource, createAsync, createAsyncLazy } from "./Async";


export type ResponseDetails = { code: number, status: string, body: string }


export const API = import.meta.env.VITE_API_URL || "http://127.0.0.1:8847"


export type RemoteOpts = { lazy?: boolean }


export type Remote<P, T> = {
	_map: Map<P, AsyncResource<T>>,
	resource: (_: P, opts?: RemoteOpts) => AsyncResource<T | null>,
}


export type Remotes = {
	/* FIXME null cancer everywhere here because async is an option ... kek */
	getWorkshopItemVersions: (_: string, o?: RemoteOpts) => AsyncResource<WorkshopItemList | null>,
	refreshWorkshopItem: (_: string, o?: RemoteOpts) => AsyncResource<WorkshopItem | WorkshopCollection | null>,
	downloadVersion: (_: number, o?: RemoteOpts) => AsyncResource<WorkshopItem | null>,
}


export const createRemote =
	<P, R,>(f: (_: P) => Promise<R>): Remote<P, R> =>
	{
		let self: Remote<P, R | null>;
		return self = {
			_map: new Map(),
			resource: (i: P, o?: RemoteOpts) => {
					let v
					if ((v = self._map.get(i)) == undefined) {
						const opts = { factory: () => f(i) }
						if (o?.lazy)
							v = createAsyncLazy<R>(opts)
						else
							v = createAsync<R | null>(f(i), opts)
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


/* GET /workshop-item/?workshopid= */
export const requestGetWorkshopItemList = async (workshopid: string): Promise<WorkshopItemList> => {
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
	 * or 200 (no redirect) for a collection
	 * ... but we can't rely on that here because the proxy
	 * follow the redirect itself will eat it */
	const obj = await res.json()

	if ("collection" in obj)
		return WorkshopCollection.parse(obj)

	else
		return WorkshopItem.parse(obj)
}


/* POST /mod-list/ */
export const requestSaveModList = async (m: SaveModList): Promise<ModList> => {
	const res = await fetch(`${API}/mod-list/`,
	                        { method: 'POST', body: JSON.stringify(m) })

	if (!res.ok)
		throw await errForResponse(res)

	return ModList.parse(await res.json())
}


/* GET /mod-list/:pk/ */
export const requestGetModList = async (pk: number | string): Promise<ModList> => {
	const res = await fetch(`${API}/mod-list/${pk}/`)

	if (!res.ok)
		throw await errForResponse(res)

	return ModList.parse(await res.json())
}


/* GET /build/ */
export const requestGetBuildList = async (): Promise<BuildSummaryList> => {
	const res = await fetch(`${API}/build/`)

	if (!res.ok)
		throw await errForResponse(res)

	return BuildSummaryList.parse(await res.json())
}


/* GET /build/:pk/ */
export const requestGetBuild = async (pk: number | string): Promise<Build> => {
	const res = await fetch(`${API}/build/${pk}/`)

	if (!res.ok)
		throw await errForResponse(res)

	return Build.parse(await res.json())
}


/* POST /build/ */
export const requestSubmitBuild = async (build: NewBuild): Promise<Build> => {
	const res = await fetch(`${API}/build/`,
	                        { method: 'POST', body: JSON.stringify(build) })

	if (!res.ok)
		throw await errForResponse(res)

	return Build.parse(await res.json())
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


export const BuildPublished = z.object({
	pk: z.number(),
	url: z.string(),
	exit_code: z.number(),
})

export type BuildPublished = z.infer<typeof BuildPublished>


export const NewBuild = z.object({
	modlist: z.number(),
})

export const Build = z.object({
  pk: z.number(),
	exit_code: z.nullish(z.number()),
	output: z.nullish(z.string()),
  fragment: z.nullish(z.object({ size: z.number() })),
})

/* a shorter Build? */
export const BuildSummary = z.object({
  pk: z.number(),
	exit_code: z.nullish(z.number()),
	item_count: z.number(),
	published: z.nullish(BuildPublished),
})

export const BuildSummaryList = z.array(BuildSummary)

export type NewBuild = z.infer<typeof NewBuild>
export type Build = z.infer<typeof Build>
export type BuildSummary = z.infer<typeof BuildSummary>
export type BuildSummaryList = z.infer<typeof BuildSummaryList>


export const SaveModList = z.object({
	items: z.array(z.number()),
})

export const ModListItem = z.object({
	pk: z.number(),
	workshopid: z.string(),
})

export const ModList = z.object({
  pk: z.number(),
	items: z.array(ModListItem),
	build: z.nullish(Build),
	published: z.nullish(BuildPublished),
})


export type SaveModList = z.infer<typeof SaveModList>
export type ModList = z.infer<typeof ModList>
export type ModListItem = z.infer<typeof ModListItem>
