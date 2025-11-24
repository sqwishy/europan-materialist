import { createContext, createSignal, createEffect, createMemo, createResource, useContext, splitProps, JSX } from 'solid-js'
import { A, useParams, useSearchParams, useNavigate } from '@solidjs/router'
import { Show, For, Index } from 'solid-js/web'

import * as Locale from "./Locale"
import * as Filters from "./Filters"
import * as Game from '../assets/bundles'

const WIKI_BASE_URL = `https://barotraumagame.com/wiki/`;
const WORKSHOP_BASE_URL = `https://steamcommunity.com/sharedfiles/filedetails/?id=`;
const TITLE_DEFAULT = /* this goofs up with hot code reloading lulz */ document.title;
const DATETIME_FMT = Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" })
const BUNDLES_BY_NAME = Object.fromEntries(Game.BUNDLES.map((bundle) => [bundle.name, bundle]))
const [DEFAULT_BUNDLE] = Game.BUNDLES;


const amt = (f: number) => f < -1
                         ? Math.abs(f)
                         : f > 1
                         ? f
                         : '';

const pct = (f: number | null) => f === null ? '' : `${100 * f}%`

const unreachable = (n: never): never => n;

// This was suppoosed to fix issues on mobile when the on-screen-keyboard pops
// up but it doesn't work and debugging on mobile is fucking impossible without
// tooling
// const elementFromViewportBottom =
// 	e => document.documentElement.clientHeight - Math.round(e.target.getBoundingClientRect().bottom);

// const dbg = v => console.log(v) || v;

const looksupLoadableBundleFromBundleParam =
  ({ bundleParam, navigate }: { bundleParam: () => string, navigate: (_: string) => void }) =>
  () => {
    let bundle, param;
    if (param = bundleParam())
      if (bundle = BUNDLES_BY_NAME[param])
        return bundle
      else
        navigate("/")
    return DEFAULT_BUNDLE
  }

async function fetchBundle({ url }: Game.LoadableBundle): Promise<Game.Bundle> {
  const res = await fetch(url)
  if (!res.ok)
    throw new Error(res.statusText)
  return await res.json();
}

type Dictionary = Record<string, string>;

type SearchContext = null | "only-consumed" | "only-produced";

// type MatchText = "substring" | "exact";

const cycleContext =
  (context: SearchContext): SearchContext =>
    context === null
    ? "only-consumed"
    : context === "only-consumed"
    ? "only-produced"
    : null

type Search = {
  text: string,
  // match: MatchText,
  context: SearchContext,
}

const searchToString =
  (f: Search): string =>
      f.context === "only-consumed"
    ? `-${f.text}`
    : f.context === "only-produced"
    ? `*${f.text}`
    : f.text

/* enforces case insensitive matching */
const stringToSearch =
  (s: string): Search =>
      s.startsWith("-")
    ? { context: "only-consumed",
        text: s.slice(1).toLowerCase() }
    : s.startsWith("*")
    ? { context: "only-produced",
        text: s.slice(1).toLowerCase() }
    : { context: null,
        text: s.toLowerCase() }

type Results = {
  entities: Game.Entity[],
  processes: Game.Process[],
};

const resultsLength = (r: Results) => r.entities.length + r.processes.length

const loadedResource = <T,>(resource: { loading: boolean, error: any, (): T | undefined }): T | null => {
  let result;
  return (   resource.loading
          || resource.error
          || undefined === (result = resource()))
         ? null
         : result
}


export type Build = { hash?: string, date: Date }


export const Page = (
  props:
    {
      setTitle: (_: string) => void,
      setSpritesHref: (_: string | undefined) => void,
      build: Build,
    }
) => {
  const navigate = useNavigate();
  const params = useParams();
  const bundleParam = () => params.bundle;
  const getCurrentLoadableBundle = createMemo(looksupLoadableBundleFromBundleParam({ bundleParam, navigate }))
  const [bundle] = createResource(getCurrentLoadableBundle, fetchBundle)
  const loadedBundle = createMemo((): Game.Bundle | null => loadedResource(bundle))

  const [getShowIntro, setShowIntro] = createSignal(true)

  const [getLanguage, setLanguage] = createSignal('English')

  const localize: Locale.ize = Locale.izes(() => loadedBundle()?.i18n[getLanguage()])
  const toEnglish: Locale.ize = Locale.izes(() => loadedBundle()?.i18n.English)

  const [searchParams, setSearchParams] = useSearchParams()

  const getSearchText = () => searchParams.q?.trim() || ''
  const setSearchText = (q: string) => setSearchParams({ q })

  const getLimit = () => parseInt(searchParams.limit, 10) || 50
  const setLimit = (limit: number) => setSearchParams({ limit })

  createEffect(() => props.setTitle(  getSearchText()
                                   ? `${getSearchText()} — ${TITLE_DEFAULT}`
                                   : TITLE_DEFAULT))

  createEffect(() => props.setSpritesHref(getCurrentLoadableBundle().sprites))

  const getSearch = createMemo((): Search => stringToSearch(getSearchText()))

  const update = (update: Update) => {
    if ("search" in update)
      setSearchText(searchToString({ ...getSearch(), text: update.search.trim() }))

    else if ("context" in update)
      setSearchText(searchToString({ ...getSearch(), context: update.context }))

    else if ("limit" in update)
      setLimit(update.limit)

    else if ("lang" in update)
      setLanguage(update.lang)

    else
      unreachable(update)
  };

  return (
    <>
      <header>
        <Show when={getShowIntro()}>
          <IntroTip dismiss={() => setShowIntro(false)} />
        </Show>

        <div class="ctl">
          <Show
            when={Game.BUNDLES.length > 1}
            fallback={
             <div class="ctl-main-item">
               <LoadOrder loadOrder={getCurrentLoadableBundle().load_order} />
             </div>
            }>
            <details class="select-bundle ctl-main-item">
              <summary>
                <LoadOrder loadOrder={getCurrentLoadableBundle().load_order} />
              </summary>

              <For each={Game.BUNDLES}>
                {(bundle) => (
                  <div class="loadable-bundle">
                    <A href={`/${bundle.name}`} classList={{"active": bundle === getCurrentLoadableBundle()}}>
                      <LoadOrder loadOrder={bundle.load_order} />
                    </A>
                  </div>
                )}
              </For>
            </details>
          </Show>

          <Show when={loadedBundle()}>
            {(bundle) =>
              <SelectLanguage
                language={getLanguage()}
                options={bundle().i18n}
                update={update} />}
          </Show>

          <Show when={loadedBundle()}>
            {(bundle) => <LoadOrderDetails loadOrder={bundle().load_order} />}
          </Show>
        </div>
      </header>

      <main>
        <Locale.Context.Provider value={[localize, toEnglish]}>
          <Show
            when={loadedBundle()}
            fallback={<Loading url={getCurrentLoadableBundle().url} resource={bundle} />}
          >
            {(bundle) => (
              <ListAndSearch
                bundle={bundle()}
                getSearch={getSearch}
                getLanguage={getLanguage}
                getLimit={getLimit}
                update={update} />
            )}
          </Show>
        </Locale.Context.Provider>
      </main>

      <footer>
        <p>
          <small>
            <a href="https://github.com/sqwishy/europan-materialist">
              github
            </a>
            <Show when={ props.build.hash }>
              {" "}
              <span class="identifier">{ props.build.hash }</span>
            </Show>
            &nbsp;— generated on { DATETIME_FMT.format(props.build.date) }
          </small>
        </p>

        <p>
          <small>
            This site uses assets and content from <a href="https://barotraumagame.com/">Barotrauma</a>.
            It is unaffiliated with <a
            href="https://undertowgames.com/">Undertow Games</a> or any
            other publisher — or anyone at all really.
            &emsp;<em class="muted">Have you been taking your Calyxanide?</em>
          </small>
        </p>
      </footer>
    </>
  )
}

const IntroTip = (props: { dismiss: () => void }) => (
  <>
    <p>
      <button class="dismiss linkish muted smol" onclick={() => props.dismiss()}>dismiss</button>
      This is a directory of Barotrauma crafting recipes.
    </p>
    <p>
      Use the <b>search at the bottom</b> of the screen or click the words inside braces like <A href="?q=meth" class="identifier">meth</A>.
    </p>
  </>
);


const LoadOrder = (props: { loadOrder: Game.Package[] }) => {
  return (
    <ol class='load-order'>
      <Index each={props.loadOrder}>
        {(item) => <LoadOrderListItem package={item()} />}
      </Index>
    </ol>
  )
}

const LoadOrderListItem = (props: { package: Game.Package, link?: boolean }) => {
  return (
    <li>
      <Show when={ props.link && props.package.steamworkshopid } fallback={ props.package.name }>
        <a href={`${WORKSHOP_BASE_URL}${props.package.steamworkshopid}`}>{ props.package.name }</a>
      </Show> <Show when={ props.package.version }>
        <span class="identifier">{ props.package.version }</span>
      </Show>
    </li>
  );
}

const LoadOrderDetails = (props: { loadOrder: Game.Package[] }) => {
  return (
    <Show when={(props.loadOrder.length || 0) > 1}>
      <details class="select-bundle">
        <summary>
          <div style="font-size: 130%; padding: 4px 6px; text-align: right">
            <i style="mask: var(--info) no-repeat 0 0/100% 100%; background: var(--muted)">&emsp;</i>
          </div>
        </summary>
        <LinkedLoadOrder loadOrder={props.loadOrder} />
      </details>
    </Show>
  )
}

const LinkedLoadOrder = (props: { loadOrder: Game.Package[] }) => {
  return (
      <For each={props.loadOrder}>
        {pkg =>
          <div class="item">
            <span class="what">
              <Show
                when={ pkg.steamworkshopid }
                fallback={ pkg.name }
                >
                <a href={`${WORKSHOP_BASE_URL}${pkg.steamworkshopid}`}>
                  { pkg.name }
                </a>
              </Show>
              <Show when={ pkg.version }>
                &nbsp;<span class="identifier">{ pkg.version }</span>
              </Show>
            </span>
            <Show when={ pkg.identifier }>
              <span>
                <A href={`?q=${ pkg.identifier }`} class="mod">{ pkg.identifier }</A>
              </span>
            </Show>
          </div>
        }
      </For>
  )
}


export const Loading = (props: { url: string, resource: any }) => {
  // createEffect(() => props.resource.error && console.error(props.resource.error))
  return (
    <div class="loading-screen">
      <Show when={props.resource.loading}>
        loading...
      </Show>
      <Show when={props.resource.error}>
        <strong>failed to load... <code>{props.url}</code></strong> {props.resource.error.toString()}
      </Show>
    </div>
  )
}

const SelectLanguage = (props: { language: string, options: Record<string, Dictionary>, update: (_: Update) => void }) => {
  return (
    <select onchange={(e) => props.update({ "lang": e.currentTarget.value })} >
       <option value="">[no localization]</option>
       <For each={Object.entries(props.options).sort()}>
         {([language, dictionary]) => (
           <option
             value={language}
             selected={props.language==language}
           >
             {dictionary[language] || language}
           </option>
         )}
       </For>
     </select>
  )
}

type Update = { "search": string }
            | { "context": SearchContext }
            | { "limit": number }
            | { "lang": string };


const getsValueByKey =
  <K extends string, V>(kv: Record<K, V>) =>
  (k: K): V => kv[k]

type GetPackageForIdentifier = (_: Game.Identifier) => string | undefined

const getsPackageNameByIdentifier =
  (entities: Game.Entity[]): GetPackageForIdentifier =>
  getsValueByKey(Object.fromEntries(entities.filter((e) => e.package)
                                            .map((e) => [e.identifier, e.package])))

/* I hate this -- TODO XXX FIXME */
export const PackageForIdentifier = createContext<GetPackageForIdentifier>((_) => undefined);


/* result listing, and search input */
export const ListAndSearch = (
  props: {
    bundle: Game.Bundle,
    getSearch: () => Search,
    getLanguage: () => string,
    getLimit: () => number,
    update: (_: Update) => void
  }
) => {
  const { getSearch, getLanguage, getLimit, update } = props /* todo does this break reactivity? */

  /* fix search bar while mouse is over it to keep it from jumping around  */
  const [getFixedSearch, setFixedSearch] = createSignal<null | number>(null)

  const filtersBySearch = filtersBundle({ getSearch, getLanguage });
  const filteredResults = createMemo((): Results => filtersBySearch(props.bundle));

  const limitsByLimit = limitsResults(getLimit);
  const limitedResults = createMemo((): Results => limitsByLimit(filteredResults()))

  const ctlcomplete = createMemo(() => {
    const identifiers = Object.values(props.bundle.entities)
                              .flatMap(e => [e.identifier, e.package].concat(e.tags))
                              .filter(Boolean)
    return [...new Set(identifiers)]
  })

  return (
    <>
      <PackageForIdentifier.Provider value={getsPackageNameByIdentifier(props.bundle.entities)}>
        <section>

          <For each={limitedResults().entities}>
            {(entity) => <Entity entity={entity} />}
          </For>

          <For each={limitedResults().processes}>
            {(p) => <Process process={p} />}
          </For>

          <div class="results-length">
            <span>
              showing <b>{resultsLength(limitedResults())}</b
              > of <b>{resultsLength(filteredResults())}</b>
            </span>
            <Show when={resultsLength(limitedResults()) < resultsLength(filteredResults())}>
              <input
                id="limit"
                type="text"
                size="4"
                inputmode="decimal"
                placeholder="limit..."
                value={getLimit()}
                onchange={(e) => update({ "limit": parseInt(e.currentTarget.value, 10) || 0 })}
              />
              <button onclick={() => update({ "limit": resultsLength(limitedResults()) + 100 })}>+100</button>
            </Show>
          </div>
        </section>
      </PackageForIdentifier.Provider>

      <div><hr/></div>

      <div
        class="ctl ctl-sticky"
        data-fixed={getFixedSearch() !== null ? '' : undefined}
        style={{ top: getFixedSearch() !== null ? `${getFixedSearch()}px` : undefined }}
        onmouseenter={(e) => setFixedSearch(e.target.getBoundingClientRect().top) }
        onmouseleave={() => setFixedSearch(null) }
      >
        <SearchFilter search={getSearch()} update={update} />
        <ContextFilter search={getSearch()} update={update} />
        <button title="up" onclick={() => window.scrollTo(0, 0)}>⬆️</button>
        <button title="down" onclick={() => document.querySelector('footer')!.scrollIntoView()}>⬇️</button>
      </div>

      {/* funny hack to prevent page height change when search above switches from sticky to fixed  */}
      <div class="surrogate"><input type="text" aria-hidden="true" /></div>

      <datalist id="ctlcomplete">
        <For each={ctlcomplete()}>
          {(value) => <option value={value} />}
        </For>
      </datalist>

    </>
  );
};

const filtersBundle =
  ({ getSearch, getLanguage } : { getSearch: () => Search, getLanguage: () => string }) =>
  (bundle: Game.Bundle) => {
    const search = getSearch();
    /* only show entities when there is search text and no search context */
    let entities: Game.Entity[] = [];
    let processes: Game.Process[] = bundle.processes;

    if (search.text.length) {
      const identifier = Filters.containsIdentifier({
        text: search.text,
        localize: getLanguage() in bundle.i18n
                ? Locale.izesToLower(() => bundle.i18n[getLanguage()])
                : undefined,
      })

      const amount = search.context === null ? undefined : Filters.amount(search.context);

      const usedIn = Filters.usedInProcess({
        part: Filters.part({
          amount,
          identifier: Filters.memo(Filters.entityToIdentifierFilter({
            bundle,
            entity: Filters.entities({ identifier }),
          })),
        })
      })

      if (search.context === null)
        entities = bundle.entities.filter(Filters.entities({ identifier }))

      processes = bundle.processes.filter(Filters.processes({ amount, identifier, usedIn }))
    }

    return { entities, processes }
  }

const limitsResults =
  (getLimit: () => number) =>
  ({ entities, processes }: Results) => {
    let limit: number;

    if ((limit = getLimit()) <= 0)
        return { entities, processes };

    entities = entities.slice(0, limit);

    limit -= entities.length;

    processes = processes.slice(0, limit);

    return { entities, processes };
  }


function SearchFilter(props: { search: Search, update: (_: Update) => void }) {
  return (
    <input
      type="text"
      class="search ctl-main-item"
      title="search by identifier or item name"
      placeholder="search..."
      accessKey="k"
      list="ctlcomplete"
      value={props.search.text}
      onchange={(e) => props.update({ "search": e.currentTarget.value })}
    />
  )
}

function ContextFilter(props: { search: Search, update: (_: Update) => void }) {
  return (
    <button
      class="context-search"
      title="cycle filter produced or consumed"
      onclick={() => props.update({ "context": cycleContext(props.search.context) })}
      data-current={props.search.context}
    >
      <span class="consumed"><span class="decoration"></span></span>
      <span class="produced"><span class="decoration"></span></span>
    </button>
  )
}


function Entity(props: { entity: Game.Entity }) {
  // package is a _reserved_ word so we can't use it lulz
  const { entity: { identifier, tags, package: mod } } = props

  return (
    <div class="entity">
      <div class="item">
        <span class="decoration"></span>
        <span class="what">
          <LocalizedIdentifier>{ identifier }</LocalizedIdentifier>
          <Show when={ mod }>
            {" "}<A href={`?q=${ mod }`} class="mod">{ mod }</A>
          </Show>
        </span>
        <Sprite identifier={ identifier } />
      </div>
      <Show when={ tags.length }>
        <div class="item">
          <span class="decoration"/>
          <span class="taglist">
            <Index each={ tags }>
              {(tag) => <Identifier>{ tag() }</Identifier>}
            </Index>
          </span>
        </div>
      </Show>
    </div>
  )
}


function Process({ process } : { process: Game.Process }) {
  const { skills, time, stations, uses, needs_recipe } = process;
  const [localize] = useContext(Locale.Context);

  return (
    <div class="process">
      {/* parts consumed */}
      <UsesList uses={uses.filter(({ amount }) => amount < 0)} />

      {/* station and time */}
      <For each={stations}>
        {(station, index) => (
          <div class="item stations">
            <span class="time">
              <Show when={time && index() == 0}>
                ⏱️ {time}s
              </Show>
            </span>
            <span class="station"><LocalizedIdentifier>{station}</LocalizedIdentifier></span>
            <Show when={index() == 0}>
              <For each={Object.entries(skills)}>
                {([skill, level]) => <span class="skill muted">{ skill } { level }</span>}
              </For>
            </Show>
            <Sprite identifier={station}/>
          </div>
        )}
      </For>

      {/* parts produced */}
      <UsesList uses={uses.filter(({ amount }) => amount >= 0)} />

      <Show when={needs_recipe}>
        <span class="needs-recipe">{localize("fabricatorrequiresrecipe")}</span>
      </Show>
    </div>
  )
}


function UsesList({ uses } : { uses: (Game.WeightedRandomWithReplacement | Game.Part)[] }) {
    return (
      <For each={uses}>
        {(used) =>
          ("weighted_random_with_replacement" in used)
            ? <WeightedRandom random={used} />
            : <Part part={used} />}
      </For>
    )
}


function Part({ part } : { part: Game.Part }) {
  const { what, amount, condition } = part;
  const [condition_min, condition_max] = condition || [null, null];
  const mod = () => useContext(PackageForIdentifier)(what)

  return (
    <div class="item part"
         classList={{ 'consumed': amount < 0, 'produced': amount > 0 }}
    >
        <span class='decoration'></span>
        <span class='amount' classList={{ 'amount-multiple': Math.abs(amount) > 1 }}>
          { amt(amount) }
        </span>
      <span class='what'>
        <LocalizedIdentifier>{ what }</LocalizedIdentifier>
        <Show when={mod()}>
          {" "}<A href={`?q=${mod()}`} class="mod">{mod()}</A>
        </Show>
      </span>
      <Show when={condition_min || condition_max}>
        <span class='condition'>
          { pct(condition_min) } ❤️ { pct(condition_max) }
        </span>
      </Show>
      <Sprite identifier={what} />
    </div>
  );
}


function Sprite(props_: { identifier: Game.Identifier } & JSX.HTMLAttributes<HTMLSpanElement>) {
  const [props, rest] = splitProps(props_, ["identifier", "class"]);

  return (
    <span
      class={`sprite ${props.class || ''}`}
      {...rest}
      data-sprite={props.identifier}
    >&emsp;</span>
  )
}


function WeightedRandom({ random } : { random: Game.WeightedRandomWithReplacement }) {
  const { weighted_random_with_replacement, amount } = random;
  const [localize] = useContext(Locale.Context);

  return (
    <>
      <div class="item part random"
           classList={{ 'consumed': amount < 0, 'produced': amount > 0 }}
      >
        <span class='decoration'></span>
        <span class='amount'>{ amt(amount) }</span>
        <span class='what'>🎲 <em>{localize('random')}</em></span>
      </div>
      {/* this ul kind of a hack so that last-of-type works */}
      <ul class="random-list">
        <For each={weighted_random_with_replacement}>
          {(used: Game.Part) => <Part part={used} />}
        </For>
      </ul>
    </>
  )
}


function LocalizedIdentifier({ children } : { children : Game.Identifier | Game.Money }) {
  const [localize, toEnglish] = useContext(Locale.Context);

  return (
      <>
        <a
          class="wiki-link"
          href={`${WIKI_BASE_URL}${toEnglish(children)}`}
          target="blank"
          rel="noopener"
        >
          {localize(children)}
        </a>
        {" "}
        <Identifier>{children}</Identifier>
      </>
  )
}


function Identifier({ children } : { children : Game.Identifier }) {
  return <A href={`?q=${children}`} class="identifier">{children}</A>
}
