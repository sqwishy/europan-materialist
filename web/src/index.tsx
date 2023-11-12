import { render } from 'solid-js/web';
import { Router } from '@solidjs/router'
import { LoadingScreen } from './Page';

render(() => <Router><LoadingScreen /></Router>, document.body!);
