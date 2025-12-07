import { Switch, Match, batch, createSignal, createResource, createMemo, createEffect } from "solid-js";
import { For, Index, Show } from "solid-js/web";
import { createStore, SetStoreFunction } from "solid-js/store"
import { useParams, useLocation, useNavigate } from "@solidjs/router";

import { z } from "zod"

import { createRemotes, wrapResource } from "./Remote";
import { workshopUrl, requestSubmitBuild, requestPing } from "./Remote";
import * as F from "./F";
import * as ItemWizard from "./ItemWizard";
import * as Misc from "./Misc";
import { Toggle } from "./Input"


type Model =
	{ name: string,
		items: ItemWizard.Params[],
		showOutput: boolean }


const isSelected = ({ isSelected }: { isSelected: boolean }) => isSelected


export const BuildNew = () => {
	const params = useParams()
	const location = useLocation()
	const navigate = useNavigate()

	const remotes = createRemotes()

	const [model, setModel] = createStore<Model>({
		name: "",
		items: [],
		showOutput: false,
	})

	const ping = wrapResource(createResource(requestPing));

	createEffect(() => {
		if (!ping.isLoading())
			setTimeout(ping.refetch, 30000)
	})

	///

	const getBuild =
		createMemo(() => params.pk ? remotes.getBuild(params.pk) : null)

	createEffect(() => {
		const build = getBuild()?.loaded()
		if (!build)
			return;
		setModel("name", build.name)
		setModel(
			"items",
			build.items
			     .map(({ workshopid, pk }) =>
			          ({ ...ItemWizard.init(workshopid), version: pk })))
	})

	createEffect(() => {
		let buildResource;
		let build;
		if (   (buildResource = getBuild())
		    && (build = buildResource.loaded())
			  && !(build.published))
			setTimeout(buildResource.refetch, 5000)
	})

	///

	const versionsToSubmit =
		createMemo(() => model.items.map(i => i.version ? i.version : []).flat())

	const versionsToDownload =
		() => versionsToSubmit().map(v => remotes.downloadVersion(v).refetch())

	/* step 1 */
	const downloadAll =
		wrapResource(createResource(
			F.ignoresFirstCall(() => Promise.all(versionsToDownload()))))

	/* step 2 */
	const submitBuild =
		wrapResource(createResource(
			F.ignoresFirstCall(() => requestSubmitBuild({ name: model.name, items: versionsToSubmit() }))))

	/* step 3 */
	const waitOnPublish =
		createMemo(() => {
				// console.log("waitOnPublish")
				let p;
				if (   (p = submitBuild.loaded()?.published)
				    || (p = getBuild()?.loaded()?.published))
					 return remotes.waitOnPublish(p)
		})

	/* steps in sequence */
	const doSubmit =
		wrapResource(createResource(
			F.ignoresFirstCall(async () => {
				submitBuild.mutate()
				navigate(`/b/`)
				await downloadAll.refetch()
				await submitBuild.refetch()
				await waitOnPublish()?.refetch()
			})))

	createEffect(() => {
		let pk;
		if (pk = submitBuild.loaded()?.pk)
			navigate(`/b/${pk}/`)
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
		() => selection().map(s => workshopUrl(s.workshopid)).join("\n")

	const [_0, { refetch: copyModsToClipboard }] =
		createResource(F.ignoresFirstCall(() => navigator.clipboard.writeText(modUrlsForClipboard())))

	const [_1, { refetch: copyOutputToClipboard }] =
		createResource(F.ignoresFirstCall(() => navigator.clipboard.writeText(getBuild()?.loaded()?.output || "")))

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
		}
	}

	return (
		<>
			<header>
				<p>
					<span class="smol muted breadcrumb">
						<span><a href="/">root</a></span>
						<span class="tt">/</span>
						<Switch>
							<Match when={!getBuild()}>
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
						<button class="linkish nowrap" title="refresh" onclick={() => ping.refetch()}>
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

				<Show when={!getBuild()?.last() && getBuild()?.isLoading()}>
					<div>
						<div class="item loading">
							<span class="decoration"></span>
							<span class="what">loading...</span>
						</div>
					</div>
				</Show>

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
							disabled={submitBuild.isLoading()}
							onclick={() => batch(() => {
								selection().forEach(({ workshopid }) => remotes.refreshWorkshopItem(workshopid).refetch())
								setModel("items", {}, "isSelected", false)
							})}>
							📥 refresh	
						</button>
						<button
							disabled={submitBuild.isLoading()}
							onclick={() => setModel("items", F.removesAt(...selectedIndexes()))}>
							❎ remove <b>{selection().length}</b>
						</button>
						<Show when={hasUnSelection()}>
							<Toggle
								disabled={submitBuild.isLoading()}
								value={isRelocating()}
								update={setRelocating}
							>🔀 {isRelocating() ? "reorder to ...?" : "reorder"}</Toggle>
						</Show>
						<button onclick={() => setModel("items", {}, "isSelected", false)}>
							unselect
						</button>
					</Show>
				</div>

				<div><hr/></div>

				<div>
					<div class="item" classList={{
						"loading": downloadAll.isLoading(),
						"success": !!downloadAll.loaded() || !!getBuild()?.last(),
					}}>
						<span class="decoration"></span>
						<span class="comfy"><span class="smol tt">#1</span> - download</span>
						<span class="tt"></span>
					</div>

					<div class="item" classList={{
						"loading": submitBuild.isLoading(),
						"success": !!submitBuild.loaded() || !!getBuild()?.last(),
					}}>
						<span class="decoration"></span>
						<span class="comfy"><span class="smol tt">#2</span> - build</span>
						<Show when={getBuild()?.last()}>
							{b =>
							<>
								<span class="clicky">
									<Toggle
										class="linkish narrow"
										value={model.showOutput}
										update={v => setModel('showOutput', v)}
									>
										🤔
									</Toggle>
								</span>
								<span class="smol">
									<i>code {b().exit_code}; {b().exit_code == 0 ? "ok" : "error"}</i>
								</span>
								<span class="smol">
									<Misc.PkTime pk={b().pk} />
								</span>
							</>
							}
						</Show>
					</div>
					<Show when={model.showOutput && getBuild()?.last()}>
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
									{f => <span class="smol"><Misc.Kb bytes={f().size} /></span>}
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
						"loading": waitOnPublish()?.isLoading(),
						"success": !!waitOnPublish()?.loaded()
					}}>
						<span class="decoration"></span>
						<span class="comfy"><span class="smol tt">#3</span> - upload</span>
						<Show when={waitOnPublish()?.loaded()}>
							{p =>
								<>
								<span>
									<a href={p().public_url} target="_blank">view</a>
								</span>
								<span class="smol">
									<i>code {p().exit_code}; {p().exit_code == 0 ? "ok" : "error"}</i>
								</span>
								</>
							}
						</Show>
						<Show when={getBuild()?.last()?.published}>
							{pk => <span class="smol"><Misc.PkTime pk={pk()} /></span>}
						</Show>
					</div>
				</div>

				<div>
					<Show when={downloadAll.error()}>
						{err => <Misc.ErrorItems title="download error" err={err()} /> }
					</Show>
					<Show when={submitBuild.error()}>
						{err => <Misc.ErrorItems title="build error" err={err()} /> }
					</Show>
					<Show when={getBuild()?.error()}>
						{err => <Misc.ErrorItems title="loading error" err={err()} /> }
					</Show>
					<Show when={waitOnPublish()?.error()}>
						{err => <Misc.ErrorItems title="upload error" err={err()} /> }
					</Show>
				</div>

				<div class="ctl">
					<input
						type="text"
						disabled={doSubmit.isLoading()}
						class="build-name ctl-main-item"
						title="name"
						placeholder="name (optional, does nothing right now) ..."
						accessKey="l"
					/>
					<button 
						disabled={doSubmit.isLoading()}
						onclick={() => doSubmit.refetch()}
					>🚢 submit</button>
				</div>

				<Show when={doSubmit.error()}>
					{err =>
						<div>
							<Misc.ErrorItems title="submit error" err={err()} />
						</div>
					}
				</Show>
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
