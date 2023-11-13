import { render } from 'solid-js/web';
import { Router } from '@solidjs/router'
import { LoadingScreen } from './Page';

render(() => <Router base={import.meta.env.BASE_URL}><LoadingScreen /></Router>, document.body!);
