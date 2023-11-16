import { createSignal, createEffect } from 'solid-js';
import { render, ErrorBoundary } from 'solid-js/web';
import { Router } from '@solidjs/router'
import { Page } from './Page';

const [title, setTitle] = createSignal(document.title);

createEffect(() => (document.title = title()));

const DumbErrorMessage = <footer><p><b>oops</b> something hecked up! maybe reload the page and hope it doesn't happen again?</p></footer>

render(() => <ErrorBoundary fallback={DumbErrorMessage}>
               <Router base={import.meta.env.BASE_URL}>
                 <Page setTitle={setTitle} />
               </Router>
             </ErrorBoundary>,
       document.body!);
