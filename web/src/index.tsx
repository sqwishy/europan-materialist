import { createSignal, createEffect } from 'solid-js';
import { render, ErrorBoundary } from 'solid-js/web';
import { Router, Routes, Route } from '@solidjs/router'
import { Page } from './Page';
import { BUNDLES } from '../assets/bundles'

/* vite does crazy shit to index.html. it will combine css files listed in <link>
 * elements based on their file extensions.
 *
 * ...  regardless of if you use link=preload vs link=stylesheet ...
 *
 * <link rel="stylesheet" href="assets/style.css"  type="text/css" />
 * <link rel="preload"    href="assets/foobar.css" type="text/css" as="style" />
 *
 * will straight up delete the preload and include foobar.css along side
 * style.css in a new file called index-123123.css and append it to the
 * <head> in a new <link rel="stylesheet"> element
 *
 * not only does that remove the preload but it destroys any chance you have at
 * suggesting to the browser what order to load things in
 *
 * this is basically just domestic terrorism just straight up ruining my markup
 *
 * as it's currently implemented, you can work around it adding the media or
 * disabled properties to the link element. but this prevents all preprocessing
 * for that element href including inlining css @imports. also, other
 * elements that don't have this workaround are simply reordered within the
 * <head> element. so you're just fucked either way
 *
 * BONUS MEME: if you try to preload src/index.jsx it will inline it
 * as a data uri ... like data:application/octet-stream ... think about
 * that for a second ... preloading a data uri ... what the fuck people */

{
  const [defaultBundle] = BUNDLES
  const link = document.createElement('link')
  link.rel = 'stylesheet';
  link.href = defaultBundle.sprites;
  document.head.append(link)
}


const BUILD = {
  hash: import.meta.env.VITE_BUILD_HASH,
  date: new Date(import.meta.env.VITE_BUILD_DATE || "2222-02-22T00:00:00-00:00"),
};

const DumbErrorMessage = <footer><p><b>oops</b> something hecked up! maybe reload the page and hope it doesn't happen again?</p></footer>

const Main = () => {
  const [title, setTitle] = createSignal(document.title);

  createEffect(() => (document.title = title()));

  if (!BUNDLES.length)
    // This shouldn't really happen?
    // The generator script shouldn't write the bundles list unless it has at
    // least one load order, even if it's just Vanilla.
    return <main><div class="loading-screen">no bundles... :C</div></main>

  // You would think it clever to `<For each={BUNDLES}>` to create a Route for
  // each bundle we have, but it turns out switching between Routes like that
  // requires the entire component to remount and it looks stupid

  return (
    <ErrorBoundary fallback={DumbErrorMessage}>
      <Router base={import.meta.env.BASE_URL}>
        <Routes>
          <Route
            path={["/", "/:bundle"]}
            element={<Page setTitle={setTitle} build={/*@once*/ BUILD} />}
          />
          <Route path="**" element={<p>FIXME</p>} />
        </Routes>
      </Router>
    </ErrorBoundary>
  )
}

render(() => <Main/>, document.body!);
