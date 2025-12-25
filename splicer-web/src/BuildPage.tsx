import { Switch, Match, batch, createSignal, createReaction, createResource, createMemo, createEffect } from "solid-js";
import { untrack } from "solid-js";
import { For, Show } from "solid-js/web";
import { createStore, SetStoreFunction } from "solid-js/store"
import { A, useParams, useLocation, useNavigate } from "@solidjs/router";

import { z } from "zod"

import * as Remote from "./Remote";

import { createRemotes } from "./Remote";
import { createAsync, createAsyncLazy } from "./Async";
import * as F from "./F";
import * as ItemWizard from "./ItemWizard";
import * as Misc from "./Misc";
import { Toggle } from "./Input"


type Model =
	{ pk: number | null,
	  items: ItemWizard.Params[],
	  showOutput: boolean }


const isSelected = ({ isSelected }: { isSelected: boolean }) => isSelected


export const BuildPage = () => {
	const params = useParams()
	const location = useLocation()
	const navigate = useNavigate()
	const remotes = createRemotes()

	// const shared = Remote.sharedMap()

	const [model, setModel] = createStore<Model>({
		pk: null,
		items: [],
		showOutput: false,
	})

	const ping = createAsync(Remote.requestPing());

	createEffect(() => {
		if (!ping.isLoading())
			if (ping.hasError())
				ping.fetchIn(Remote.requestPing, 9_000)
			else
				ping.fetchIn(Remote.requestPing, 90_000)
	})

	///

	/* the "ModList" embeds the complete build and published state */
	const modList = createAsyncLazy<Remote.ModList>()
	const build = () => modList.last()?.build
	const published = () => modList.last()?.published

	const selectedWorkshopVersions =
		() => model.items.map(i => i.version ? i.version : []).flat()

	const saving = createAsyncLazy<Remote.ModList>({ tracks: () => (params.pk, true) })
	/* step 1 */
	const downloadingAll = createAsyncLazy({ tracks: () => submitting.isLoading() })
	/* step 2 */
	const building = createAsyncLazy<Remote.Build>({ tracks: () => submitting.isLoading() })
	/* step 3 */
	const publishing = createAsyncLazy<Remote.ModList>({ tracks: () => (model.pk, true) })
	/* save-download-build in sequence */
	const submitting = createAsyncLazy();

	const isSavingOrSubmitting =
		() => saving.isLoading() || submitting.isLoading()
	
	/* fetch mod list on navigation if our model is stale */
	createEffect(() => {
		let pk;
		if (   (pk = Number(params.pk))
		    && (untrack(() => model.pk) != pk)) {
			modList.fetch(Remote.requestGetModList(pk))
		}
	})

	/* after modList is fetched, re-init items state when it matches our path */
	createEffect(() => {
		let pk;
		let list;
		/* when we have a path param */
		if (   (pk = Number(params.pk))
		/* and it differs from our model */
		    && (model.pk != pk)
		/* and having fetched a mod list */
		    && (list = modList.loaded())
		/* and which matches our path param  */
		    && (pk == list.pk)) {
			/* then update the model pk and overwrite item state */
			let { pk, items } = list;
			batch(() => {
				setModel("pk", pk)
				setModel("items", items.map(({ workshopid, pk }) =>
				                            ({ ...ItemWizard.init(workshopid), version: pk })))
			})
		}
	})

	///

	const save =
		() =>
		saving.fetch(Remote.requestSaveModList({ items: selectedWorkshopVersions() }))

	/* after saving, update the our URL */
	createEffect(() => {
		const list = saving.loaded()
		if (list)
			batch(() => {
				modList.insert(list)
				setModel("pk", list.pk)
				navigate(`/b/${list.pk}`, { scroll: false })
			})
	})

	const submit =
		() =>
		submitting.fetch(save().then(l => {
			downloadAll(l).then(() => {
				submitBuild(l)
			})
		}))

	const downloadAll =
		({ items }: Remote.ModList) =>
		downloadingAll.fetch(Promise.all(items.map(({ pk }) => remotes.downloadVersion(pk).refetch())))

	const submitBuild =
		({ pk }: Remote.ModList) =>
		building.fetch(Remote.requestSubmitBuild({ modlist: pk }))

	createEffect(() => {
		const b = building.loaded()
		if (b?.pk)
			modList.fetch(Remote.requestGetModList(b.pk))
	})

	createEffect(() => {
		const l = modList.loaded()
		if (   l?.pk
		    && l.build
		    && !l.published
		    && !publishing.isLoading())
			publishing.fetch(F.zzzMs(6_000)
			                  .then(() => Remote.requestGetModList(l.pk)))
			/* this should just be a separate createEffect maybe i guess idk lmao */
			          .then(l => modList.insert(l))
			/* catch cancellation */
			          .catch(() => null)
	})

	///

	const [isRelocating, setRelocating] = createSignal(false);

	const selection = createMemo(() => model.items.filter(isSelected))
	const selectedIndexes = () => model.items
		.map(({ isSelected }, index) => isSelected ? index : [])
		.flat()
	const hasSelection = () => selection().length > 0
	const hasUnSelection = () => selection().length < model.items.length

	///

	const modUrlsForClipboard =
		() => selection().map(s => Remote.workshopUrl(s.workshopid)).join("\n")

	const [_0, { refetch: copyModsToClipboard }] =
		createResource(F.ignoresFirstCall(() => navigator.clipboard.writeText(modUrlsForClipboard())))

	const [_1, { refetch: copyOutputToClipboard }] =
		createResource(F.ignoresFirstCall(() => navigator.clipboard.writeText(modList.loaded()?.build?.output || "")))

	///

	const doUpdate = (
		{ item, index }: { item: ItemWizard.Update, index: number }
	) => {

    if ("remove" == item) {
			setModel("items", F.removesAt(index))
      return
    }

		if ("relocate" == item) {
			/* Remove the selection and reinsert them at the index destination.
			 * If an item is selected before the destination,
			 * then insert just after destination.  */
			batch(() => {
				const selectedAhead = model.items
					.slice(0, index)
					.filter(isSelected)
					.length;
				const destinationIndex =
					selectedAhead > 0 ? index + 1 - selectedAhead : index
				const selectedIndexes = model.items
					.map(({ isSelected }, index) => isSelected ? index : [])
					.flat()
				const items = F.getsAt(...selectedIndexes)(model.items);
				setModel("items", selectedIndexes, "isSelected", false)
				setModel("items", F.removesAt(...selectedIndexes))
				setModel("items", F.insertsAt(destinationIndex)(items))
				setRelocating(false);
			})
			return
		}

		if ("isSelected" in item) {
			setModel("items", index, item)
			return
		}

		if ("version" in item) {
			setModel("items", index, item)
			return
		}

		else if ("replaceCollection" in item) {
			let items = item.replaceCollection
				.map(ItemWizard.init)
			setModel("items", F.removesAt(index))
			setModel("items", F.insertsAt(index)(items))
			return
		}

		else {
			// F.unreachable({ item, index })
			let _: never = item;
		}
	}

	return (
		<>
			<header>
				<p>
					<span class="smol muted breadcrumb">
						<span><A href="/">directory</A></span>
						<span class="tt">/</span>
						<Switch>
							<Match when={!params.pk}>
								<span>new load order</span>
							</Match>
							<Match when={params.pk}>
								<span>{params.pk}</span>
							</Match>
							<Match when={true}>
								<span>...</span>
							</Match>
						</Switch>
					</span>
					<span class="smol connection-status muted">
						<button class="linkish nowrap"
							title="refresh"
							onclick={() => !ping.isLoading() && ping.fetch(Remote.requestPing())}>
							<Switch>
								<Match when={ping.last()}>
									online
								</Match>
								<Match when={ping.hasError()}>
									❗ offline
								</Match>
								<Match when={ping.isLoading()}>
									...
								</Match>
							</Switch>
						</button>
					</span>
				</p>
			</header>

			<main>
				<div><hr/></div>

				<Switch>
					<Match when={modList.isLoading()}>
						<div>
							<div class="item loading">
								<span class="decoration"></span>
								<span class="comfy caps">loading...</span>
							</div>
						</div>
					</Match>
					<Match when={true}>
						<div>
							<form onsubmit={itemsForm(v => setModel("items", F.appends(...v)))}>
								<input
									type="text"
									class="workshopid"
									title="workshop item id or URL"
									placeholder="workshop item or URL..."
									accessKey="k"
								/>
								<button type="submit">add</button>
							</form>
						</div>
					</Match>
				</Switch>

				<section>
					<For each={model.items}>
						{(p: ItemWizard.Params, index) =>
							<ItemWizard.View {...p}
								canSelect={!isRelocating()}
								canRelocate={(isRelocating() && !p.isSelected)}
								update={item => doUpdate({ item, index: index() })}
								/* hacky? */
								remotes={remotes}
								/>
						}
					</For>
				</section>

				<div class="ctl ctl-sticky ctl-wrap">
					<Show when={hasUnSelection()}>
						<button
							onclick={() => setModel("items", {}, "isSelected", true)}>
							select all
						</button>
					</Show>
					<Show when={hasSelection()}>
						<button
							onclick={() => copyModsToClipboard()}>
							📋 copy to clipboard
						</button>
						<button
							disabled={isSavingOrSubmitting()}
							onclick={() => batch(() => {
								selection().forEach(({ workshopid }) => remotes.refreshWorkshopItem(workshopid).refetch())
								setModel("items", {}, "isSelected", false)
							})}>
							📥 refresh
						</button>
						<button
							disabled={isSavingOrSubmitting()}
							onclick={() => setModel("items", F.removesAt(...selectedIndexes()))}>
							❎ remove <b>{selection().length}</b>
						</button>
						<Show when={hasUnSelection()}>
							<Toggle
								disabled={isSavingOrSubmitting()}
								value={isRelocating()}
								update={setRelocating}
							>🔀 {isRelocating() ? "reorder to ...?" : "reorder"}</Toggle>
						</Show>
						<button onclick={() => setModel("items", {}, "isSelected", false)}>
							unselect
						</button>
					</Show>
				</div>

				<div>
					<div class="item" classList={{
						"loading": downloadingAll.isLoading(),
						"success": !!downloadingAll.loaded() || !!build(),
					}}>
						<span class="decoration"></span>
						<span class="comfy"><span class="smol tt">#1</span> - <span class="caps">download</span></span>
						<span class="tt"></span>
					</div>

					<div class="item" classList={{
						"loading": building.isLoading(),
						"success": !!build(),
					}}>
						<span class="decoration"></span>
						<span class="comfy"><span class="smol tt">#2</span> - <span class="caps">build</span></span>
						<Show when={build()}>
							{b =>
							<>
								<span class="clicky">
									<Toggle
										class="linkish narrow"
										value={model.showOutput}
										update={v => setModel('showOutput', v)}
									>
										logs
									</Toggle>
								</span>
								<span class="smol">
									<Show when={b().exit_code != null}>
										<Misc.Exit code={b().exit_code!} />
									</Show>
								</span>
								<span class="smol"><Misc.PkTime pk={b().pk} /></span>
							</>
							}
						</Show>
					</div>
					<Show when={model.showOutput && build()}>
					{b =>
					<>
					<div class="item">
						<span class="decoration"></span>
						<span class="comfy">
							<button
								class="linkish narrow"
								onclick={() => copyOutputToClipboard()}>
								📋 copy to clipboard
							</button>
						</span>
						<Show when={b().fragment}>
							{f => <span class="smol">bundle <Misc.Kb bytes={f().size} /></span>}
						</Show>
					</div>
					<div class="item">
						<span class="decoration"></span>
						<pre>{b().output}</pre>
					</div>
					</>
					}
					</Show>

					<div class="item" classList={{
						"loading": publishing.isLoading(),
						"success": !!published(),
					}}>
						<span class="decoration"></span>
						<span class="comfy"><span class="smol tt">#3</span> - <span class="caps">upload</span></span>
						<Show when={published()}>
							{p =>
								<>
								<span>
									<a href={p().url} target="_blank">view</a>
								</span>
								<span class="smol"><Misc.Exit code={p().exit_code} /></span>
								<span class="smol"><Misc.PkTime pk={p().pk} /></span>
								</>
							}
						</Show>
					</div>
				</div>

				<div>
					<Show when={modList.error()}>
						{err => <Misc.ErrorItems title="loading error" err={err()} /> }
					</Show>
					{/* <Show when={downloadingAll.error()}> */}
					{/* 	{err => <Misc.ErrorItems title="download error" err={err()} /> } */}
					{/* </Show> */}
					<Show when={building.error()}>
						{err => <Misc.ErrorItems title="build error" err={err()} /> }
					</Show>
					<Show when={publishing.error()}>
						{err => <Misc.ErrorItems title="upload error" err={err()} /> }
					</Show>
				</div>

				<div class="ctl">
					<span class="smol muted ctl-main-item">
						<Show when={modList.last()?.pk /*Number(params.pk)*/}>
							{pk =>
								<Show when={!saving.isLoading()} fallback={"saving..."}>
									<>saved at <Misc.PkTime pk={pk()} /></>
								</Show>
							}
						</Show>
					</span>
					<button
						disabled={isSavingOrSubmitting()}
						onclick={() => save()}
					>💾 save</button>
					<button
						disabled={isSavingOrSubmitting()}
						onclick={() => submit()}
					>🚢 submit</button>
				</div>

				<Show when={saving.error()}>
					{err =>
						<div>
							<Misc.ErrorItems title="save error" err={err()} />
						</div>
					}
				</Show>
				{/* <Show when={submitting.error()}> */}
				{/* 	{err => */}
				{/* 		<div> */}
				{/* 			<Misc.ErrorItems title="submit error" err={err()} /> */}
				{/* 		</div> */}
				{/* 	} */}
				{/* </Show> */}
			</main>
		</>
	);
}


const itemsForm =
	(set: (_: ItemWizard.Params[]) => void) =>
	(e: Event) =>
{
	let form, input

	e.preventDefault()

	if (   !(form = e.currentTarget as HTMLFormElement | null)
	    || !(input = form.querySelector(".workshopid") as HTMLInputElement | null))
		return;

	let update = input.value
		.split(" ")
		.filter(s => s.length)
		.map(ItemWizard.init)

	set(update)

	input.value = "";
}
