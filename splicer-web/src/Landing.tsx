import { createResource } from "solid-js";
import { For, Show } from "solid-js/web";
import { A } from '@solidjs/router'

import { wrapResource, requestGetBuildList } from "./Remote";
import * as Misc from "./Misc";

export const Landing = () => {
	const list = wrapResource(createResource(requestGetBuildList));

	console.log(list)

	return (
		<>
		<header>
			<p class="smol muted breadcrumb">
				<span><a href="/">directory</a></span>
			</p>
		</header>
		<main>
			<div><hr/></div>
			<p><a class="buttonish" href="/b/">new load order</a></p>

			<Show when={list.error()}>
				{err => <div><Misc.ErrorItems title="list error" err={err()} /></div> }
			</Show>

			<div>
				<ol>
				<Show when={list.loaded()}>
					{l =>
						<For each={l()}> 
							{build =>
							<li>
								<span>{build.item_count}x </span>
								<span class="comfy">
									<A href={`/b/${build.pk}`}>
										{/* <span class="tt">{build.pk}</span> */}
										{/* &emsp; */}
										<span class="smol">
										 <Misc.PkTime pk={build.pk} />
										</span>
									</A>
									<a href="#">
										view
									</a>
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
