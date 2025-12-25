import { createResource } from "solid-js";
import { For, Show } from "solid-js/web";
import { A } from '@solidjs/router'

import { requestGetBuildList } from "./Remote";
import { createAsync } from "./Async";
import * as Misc from "./Misc";

export const Landing = () => {
	const list = createAsync(requestGetBuildList());

	return (
		<>
		<header>
			<p class="smol muted breadcrumb">
				<span><a href="/">directory</a></span>
			</p>
		</header>
		<main>
			<div><hr/></div>
			<p><a class="buttonish highlight-button" href="/b/">new load order</a></p>

			<Show when={list.error()}>
				{err => <div><Misc.ErrorItems title="list error" err={err()} /></div> }
			</Show>

			<div>
				<ol>
				<Show when={list.loaded()}>
					{l =>
						<For each={l()}> 
							{build =>
							<li class="item">
								{/* <span>{build.item_count}x </span> */}
								<span class="comfy">
									<A href={`/b/${build.pk}`}>
										 <Misc.PkTime pk={build.pk} />
									</A>
									&emsp;
									<span class="identifier"><b>{build.item_count}×</b> mods</span>
								</span>
								<span>
									<Show when={build.published}>
										{p => <a href={p().url}>view</a>}
									</Show>
								</span>
								{/* <span class="smol"> */}
								{/* 	<i>code {build.exit_code}; {build.exit_code == 0 ? "ok" : "error"}</i> */}
								{/* </span> */}
							</li>
							}
						</For>
					}
				</Show>
				</ol>
			</div>

			<Show when={list.isLoading()}>
				<p>loading?</p>
			</Show>
		</main>
		</>
	)
}
