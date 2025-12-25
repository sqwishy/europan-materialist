import { createResource, createSignal, createEffect, createMemo, Switch, Match } from "solid-js";
import { untrack } from "solid-js";
import { For, Index, Show } from "solid-js/web";
import { createStore } from "solid-js/store"

import { z } from "zod"

import { workshopUrl, ResponseDetails, Remotes, WorkshopItem, WorkshopCollection } from "./Remote";
import { Toggle, Radio } from "./Input"
import { createAsyncLazy, createAsync } from "./Async"
import * as Remote from "./Remote"
import * as F from "./F"
import * as Misc from "./Misc"


export type Params =
	{
		workshopid: string,
		version: number | null,
		update?: (_: Update) => void,
		canSelect?: boolean,
		canRelocate?: boolean,
		remotes?: Remotes,
	}
	& Selectable

type Selectable = { isSelected: boolean }

export const init =
	(workshopid: string): Params =>
	({ workshopid, isSelected: false, version: null })


export type Update = 
	| Selectable
	| { version: number | null }
	| { replaceCollection: string[] }
	| "relocate"
	| "remove"


export const View = (params: Params) => {
  const versions =
		createMemo(() => params.remotes?.getWorkshopItemVersions(params.workshopid))

  const refreshing =
		createMemo(() => params.remotes?.refreshWorkshopItem(params.workshopid, { lazy: true }) )

	const downloading =
		createMemo(() => {
			const version = params.version
			if (version != null)
				return params.remotes?.downloadVersion(version, { lazy: true })
		})

	const collection =
		createMemo(() => {
			const l = refreshing()?.last()
			if (l && "collection" in l)
				return l
		})

	const anyLoading =
		() => versions()?.isLoading()
		   || refreshing()?.isLoading()
		   || downloading()?.isLoading()

	const latestVersion =
		createMemo(() => versions()?.last()?.[0]);

	/* if we have never seen the workshop item before,
	 * then try to refresh it from the steam workshop */
	createEffect(() => {
		let e = versions()?.error()
		if (e?.cause?.code == 404)
			refreshing()?.refetch()
	})

	/* after refreshing a workshop item from steam,
	 * update our list of workshop item versions */
	createEffect(() => {
		const w = refreshing()?.loaded()
		if (w && !("collection" in w))
			versions()?.refetch()
	})

	createEffect(() => {
		if (downloading()?.loaded())
			versions()?.refetch()
	})

	/* default the selected version to the most recent */
	createEffect(() => {
		let version = latestVersion()?.pk;
		/* untrack to avoid re-selecting when the user explicitly de-selects... */
		if (version && !untrack(() => params.version))
			params.update?.({ version })
	})

	return (
		<div classList={{"is-selected": params.isSelected}}>
			<Switch>
				<Match when={latestVersion()}>
					{item =>
						<>
						<div class="item" classList={{"loading": anyLoading()}}>
							<span class="decoration"></span>
							<span class="comfy"> 
								<a href={workshopUrl(item().workshopid)}>{item().title}</a>
								{" "}
								<span class="smol"> by <Authors authors={item().authors}/></span>
							</span>
							<span class="clicky">
								<Show when={params.canSelect}>
									<Toggle
										class="linkish square"
										value={params.isSelected}
										update={isSelected => params.update?.({ isSelected })}
									> </Toggle>
								</Show>
								<Show when={params.canRelocate}>
									<button
										class="toggle linkish square"
										onclick={() => params.update?.("relocate")}
									>🔀</button>
								</Show>
							</span>
						</div>
						<For each={versions()?.last()}>
							{(item, i) =>
								<div class="item" classList={{"loading": anyLoading()}}>
									<span class="decoration"></span>
									<span class="comfy">
										<Misc.UnixTime unix={item.version} />
									</span>
									<span class="version">
										<Show when={item.content} fallback={<>&ZeroWidthSpace;</>}>
											<span class="identifier">v{item.content?.version || "???"}</span>
										</Show>
									</span>

									<Show when={i() == 0 && !item.file && downloading()}>
										{r => 
											<span class="smol">
												<button
													class="linkish narrow"
													disabled={r().isLoading()}
													onclick={() => r().refetch()}>
													download file
												</button>
											</span>
										}
									</Show>
									<span class="clicky">
										<Radio
											class="linkish square"
											value={params.version == item.pk}
											update={b => params.update?.({ version: b ? item.pk : null })}
										> </Radio>
									</span>
								</div>
							}
						</For>
						<Show when={downloading()?.error()}>
							{err => <Misc.ErrorItems title={"download error"} err={err()} />}
						</Show>
						<Show when={refreshing()?.error()}>
							{err => <Misc.ErrorItems title={"refresh error"} err={err()} />}
						</Show>
						</>
					}
				</Match>

        <Match when={collection()}>
					{c =>
						<div class="item">
							<span class="decoration"></span>
							<span class="comfy"> 
								<a href={workshopUrl(c().workshopid)}>{c().title}</a>
								{" "}
								<span class="smol"> by <Authors authors={c().authors}/></span>
							</span>
							<span class="clicky">
								<button
									class="linkish narrow"
									onclick={() => params.update?.({ replaceCollection: c().collection.slice() })}>
									add <b>{c().collection.length}</b>
									{" "}{c().collection.length == 1 ? "item" : "collection"}
								</button>
								<Show when={params.canSelect}>
									<Toggle
										class="linkish square"
										value={params.isSelected}
										update={isSelected => params.update?.({ isSelected })}
									> </Toggle>
								</Show>
								<Show when={params.canRelocate}>
									<button
										class="toggle linkish square"
										onclick={() => params.update?.("relocate")}
									>🔀</button>
								</Show>
							</span>
						</div>
					}
				</Match>

				<Match when={anyLoading()}>
					<div class="item loading">
						<span class="decoration"></span>
						<span class="comfy nowrap workshopid">{params.workshopid}</span>
					</div>
				</Match>

				<Match when={   (refreshing()?.error() && refreshing())
				             || (versions()?.error() && versions())}>
					{res =>
						<>
						<Misc.ErrorItems title={params.workshopid} err={res().error()} />
						<div class="item error">
							<span class="decoration"></span>
							<span class="clicky">
								<button class="linkish narrow" onclick={() => res()?.refetch()}>
									retry
								</button>
								<Show when={params.update}>
								<button class="linkish narrow" onclick={() => params.update?.("remove")}>
									remove
								</button>
								</Show>
							</span>
						</div>
						</>
					}
				</Match>

				<Match when={true}>
					<span><p>TODO? <span class="smol">oh no, how did i get here, i don't know what to show :(</span></p></span>
				</Match>
			</Switch>
		</div>
	)
}

const Authors = (params: { authors: string[] }) => {
	const atLeastOne =
		(): string[] => 
		params.authors.length > 0 ? params.authors : ["???"]
	return (
		<For each={atLeastOne()}>
			{(author, i) =>
				<>
					{i() > 0 ? ", " : ""}
					<span class="nowrap author">{author}</span>
				</>
			}
		</For>
	)
}
