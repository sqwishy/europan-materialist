/* @refresh reload */

import { render, ErrorBoundary, Show } from 'solid-js/web';
import { Router, Route, Navigate } from '@solidjs/router'

import { BuildPage } from "./BuildPage"
import { Landing } from "./Landing"
import * as Misc from "./Misc";

const BUILD = {
  hash: import.meta.env.VITE_BUILD_HASH,
  date: new Date(import.meta.env.VITE_BUILD_DATE || "2222-02-22T00:00:00-00:00"),
};

const DumbErrorMessage = <footer><p><b>oops</b> something hecked up! maybe reload the page and hope it doesn't happen again?</p></footer>

const Main = (props: {}) => {
	return (
		<ErrorBoundary fallback={DumbErrorMessage}>
			<Router base={import.meta.env.BASE_URL} /*explicitLinks={true}*/>
				<Route path="/" component={Landing} />
				<Route path={["/b/", "/b/:pk/"]} component={BuildPage} />
			</Router>

			<footer>
				<div><hr /></div>

				<p>
					<small>
						<a href="https://github.com/sqwishy/europan-materialist">
							github
						</a>
						<Show when={ BUILD.hash }>
							{" "}
							<span class="identifier">{ BUILD.hash }</span>
						</Show>
						&nbsp;— <Misc.Time time={BUILD.date} />
					</small>
				</p>
			</footer>
		</ErrorBoundary>
	)
}

render(() => <Main/>, document.body!);
