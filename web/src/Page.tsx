import { createSignal, createEffect, createMemo, createResource, useContext, splitProps, JSX } from 'solid-js'
import { A, useParams, useSearchParams } from '@solidjs/router'
import { Show, For, Index } from 'solid-js/web'

import * as Locale from "./Locale"
import * as Filters from "./Filters"
import * as Game from '../assets/bundles'

export async function fetchBundle(url: string): Promise<Game.Bundle> {
  const res = await fetch(url)
  if (!res.ok)
    throw new Error(res.statusText)
  return await res.json();
}

const WIKI_BASE_URL = `https://barotraumagame.com/wiki/`;
const WORKSHOP_BASE_URL = `https://steamcommunity.com/sharedfiles/filedetails/?id=`;
const TITLE_DEFAULT = /* this goofs up with hot code reloading lulz */ document.title;
const DATETIME_FMT = Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" })


const amt = (f: number) => f < -1
                         ? Math.abs(f)
                         : f > 1
                         ? f
                         : '';
const pct = (f: number | null) => f === null ? '' : `${100 * f}%`
const unreachable = (n: never): never => n;
// const dbg = v => console.log(v) || v;


type Dictionary = Record<string, string>;
type SearchContext = null | "only-consumed" | "only-produced";

const cycleContext =
  (context: SearchContext) => context === null
                            ? "only-consumed"
                            : context === "only-consumed"
                            ? "only-produced"
                            : null

type Search = {
  substring: string,
  context: SearchContext,
}

type Results = {
  entities: [/* identifier */ Game.Identifier, /* tags */ Game.Identifier[]][],
  processes: Game.Process[],
};


const searchToString =
  (f: Search): string =>
      f.context === "only-consumed"
    ? `-${f.substring}`
    : f.context === "only-produced"
    ? `*${f.substring}`
    : f.substring


/* enforces case insensitive matching */
const stringToSearch =
  (s: string): Search =>
      s.startsWith("-")
    ? { context: "only-consumed",
        substring: s.slice(1).toLowerCase() }
    : s.startsWith("*")
    ? { context: "only-produced",
        substring: s.slice(1).toLowerCase() }
    : { context: null,
        substring: s.toLowerCase() }


const resultsLength = (r: Results) => r.entities.length + r.processes.length


export type Build = { hash?: string, date: Date }


// export const Page = (props: { setTitle: (_: string) => void, build: Build }) => {
//   const bundles = /* @once */ Game.BUNDLES;
//   return (
//     <>
//     <ol class='packages'>
//       <For each={Game.BUNDLES}>
//         {(bundle) => 
//           <li>
//             <A class="bundle-select" href="?">
//               <ol class='load-order'>
//                 <Index each={bundle.load_order}>
//                   {(item) => <li>{ item().name } <span class="identifier">{ item().version }</span></li>}
//                 </Index>
//               </ol>
//             </A>
//           </li>
//          }
//       </For>
//     </ol>
//   </>
//   )
// }

export const Page = (self: { setTitle: (_: string) => void, build: Build }) => {
  const [defaultBundle] = Game.BUNDLES;
  const bundleUrl = defaultBundle.bundle;
  const [bundle] = createResource(() => fetchBundle(bundleUrl))
  const hasResource = createMemo(() => !bundle.loading && !bundle.error && bundle());

  const params = useParams();
  console.log({ ...params })

  return (
    <Show when={hasResource()} keyed fallback={<Loading url={bundleUrl} resource={bundle} />}>
      {(bundle) =>
        <>
          <main>
            <Content bundle={bundle} setTitle={self.setTitle} />
          </main>

          <footer>
            <p>
              <small>
                <a href="https://github.com/sqwishy/europan-materialist">
                  github
                </a>
                <Show when={ self.build.hash }>
                  {" "}
                  <span class="identifier">{ self.build.hash }</span>
                </Show>
                \ — generated on { DATETIME_FMT.format(self.build.date) }
              </small>
            </p>

            <div>
              <small>
                <ol class='load-order'>
                  <Index each={bundle.load_order}>
                    {(item) => <LoadOrderListItem {...item()} />}
                  </Index>
                </ol>
              </small>
            </div>

            <hr/>

            <p>
              This is a directory of Barotrauma crafting recipes.
              Use the <b>search at the bottom</b> of the screen or click the words inside braces like <A href="?q=meth" class="identifier">meth</A>.
            </p>

            <p>
              <small>
                This site uses assets and content from <a href="https://barotraumagame.com/">Barotrauma</a>.
                It is unaffiliated with <a href="https://undertowgames.com/">Undertow Games</a> or anyone else.
                It is impartial to all clown and husk related matters.
              </small>
            </p>
          </footer>
        </>
      }
    </Show>
  )
}


const LoadOrderListItem = (props: Game.Package) => {
  return (
    <li>
      <Show when={ props.steamworkshopid } fallback={ props.name }>
        <a href={`${WORKSHOP_BASE_URL}${props.steamworkshopid}`}>{ props.name }</a>
      </Show> <Show when={ props.version }>
        <span class="identifier">{ props.version }</span>
      </Show>
    </li>
  );
}


export const Loading = (self: { url: string, resource: any }) => {
  // createEffect(() => self.resource.error && console.error(self.resource.error))
  return (
    <>
      <main>
        <div class="loading-screen">
          <Show when={self.resource.loading}>
            loading...
          </Show>
          <Show when={self.resource.error}>
            <strong>failed to load... <code>{self.url}</code></strong> {self.resource.error.toString()}
          </Show>
        </div>
      </main>
    </>
  )
}


type Update = { "search": string }
            | { "context": SearchContext }
            | { "limit": number }
            | { "lang": string };


/* language select, result listing, and search input */
export const Content = (self: { bundle: Game.Bundle, setTitle: (_: string) => void }) => {

  /* fix search bar while mouse is over it to keep it from jumping around  */
  const [getFixedSearch, setFixedSearch] = createSignal<null | number>(null)

  const [getLanguage, setLanguage] = createSignal('English')

  const localize: Locale.ize = Locale.izes(() => self.bundle.i18n[getLanguage()])
  const toEnglish: Locale.ize = Locale.izes(() => self.bundle.i18n.English)

  const [searchParams, setSearchParams] = useSearchParams()

  const getSearchText = () => searchParams.q?.trim() || ''
  const setSearchText = (q: string) => setSearchParams({ q })

  const getLimit = () => parseInt(searchParams.limit, 10) || 50
  const setLimit = (limit: number) => setSearchParams({ limit })

  createEffect(() => self.setTitle(  getSearchText()
                                   ? `${getSearchText()} — ${TITLE_DEFAULT}`
                                   : TITLE_DEFAULT))

  const getSearch = createMemo((): Search => stringToSearch(getSearchText()))

  const filteredResults = createMemo((): Results => {
    const search = getSearch();

    /* don't show entities unless there is a substring search */
    /* why can't fucking typescript infer the type for `entities` when this return type is explicit ??? */
    let entities: [string, string[]][] = [];
    let processes = self.bundle.processes

    if (search.substring.length) {
      const identifier = Filters.identifier({
        substring: search.substring,
        localize: getLanguage() in self.bundle.i18n
                ? Locale.izesToLower(() => self.bundle.i18n[getLanguage()])
                : undefined,
      })
      const amount = search.context === null
                   ? undefined
                   : Filters.amount(search.context)
      const part = Filters.part({ amount, identifier })
      const usedIn = Filters.usedInProcess({ part })

      entities = Object.entries(self.bundle.tags_by_identifier)
                       .filter(Filters.entities({ amount, identifier }))
      processes = processes.filter(Filters.processes({ amount, identifier, usedIn }))
    }

    return { entities, processes }
  })

  const limitedResults = createMemo((): Results => {
    let limit: number;
    let { entities, processes } = filteredResults();

    if ((limit = getLimit()) <= 0)
        return { entities, processes };

    entities = entities.slice(0, limit);

    limit -= entities.length;

    processes = processes.slice(0, limit);

    return { entities, processes };
  })

  const complete = createMemo(() => {
    const tags_by_identifier = self.bundle.tags_by_identifier;
    const allTags = Object.values(tags_by_identifier).flat();
    const allIdentifiers = allTags.concat(Object.keys(tags_by_identifier))
    return [...new Set(allIdentifiers)]
  })

  const update = (update: Update) => {
    if ("search" in update)
      setSearchText(searchToString({ ...getSearch(), substring: update.search.trim() }))

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
      {/* language select */}
      <SelectLanguage language={getLanguage()} options={self.bundle.i18n} update={update} />

      <hr/>

      <Locale.Context.Provider value={[localize, toEnglish]}>
        <EntityAndProcessList results={limitedResults()} />
      </Locale.Context.Provider>

      <section class="results-length">
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
      </section>

      <hr/>

      <section
        class="cmd"
        data-fixed={getFixedSearch() !== null ? '' : undefined}
        style={{ top: getFixedSearch() !== null ? `${getFixedSearch()}px` : undefined }}
        onmouseenter={(e) => setFixedSearch(e.target.getBoundingClientRect().top) }
        onmouseleave={() => setFixedSearch(null) }
      >
        <SearchFilter search={getSearch()} update={update} />
        <ContextFilter search={getSearch()} update={update} />
        {/* <button onclick={() => window.scrollTo(0, 0)}>⬆️</button> */}
      </section>

      {/* funny hack to prevent page height change when search above switches from sticky to fixed  */}
      <p class="surrogate"><input type="text"/></p>

      <datalist id="cmdcomplete">
        <For each={complete()}>
          {(value) => <option value={value} />}
        </For>
      </datalist>

    </>
  );
};


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


const EntityAndProcessList = (props: { results: Results }) => {
  return (
    <section>
      <For each={props.results.entities}>
        {([identifier, tags]) => <Entity identifier={identifier} tags={tags} />}
      </For>
      <For each={props.results.processes}>
        {(p) => <Process process={p} />}
      </For>
    </section>
  )
}


function SearchFilter(props: { search: Search, update: (_: Update) => void }) {
  const [self, _] = splitProps(props, ["search", "update"]);
  return (
    <input
      type="text"
      class="search"
      placeholder="search..."
      accessKey="k"
      list="cmdcomplete"
      value={self.search.substring}
      onchange={(e) => self.update({ "search": e.currentTarget.value })}
    />
  )
}

function ContextFilter(props: { search: Search, update: (_: Update) => void }) {
  const [self, _] = splitProps(props, ["search", "update"]);
  return (
    <button
      class="context-search"
      onclick={() => self.update({ "context": cycleContext(self.search.context) })}
      data-current={self.search.context}
    >
      <span class="consumed"><span class="decoration"></span></span>
      <span class="produced"><span class="decoration"></span></span>
    </button>
  )
}


function Entity({ identifier, tags } : { identifier: Game.Identifier, tags: Game.Identifier[] }) {
  return (
      <div class="entity">
        <div class="item">
          <span class="decoration"></span>
          <span class="what"><LocalizedIdentifier>{ identifier }</LocalizedIdentifier></span>
          <Sprite identifier={identifier} />
        </div>
        <div class="item">
          <span class="decoration"/>
          <span class="taglist">
            <Index each={tags}>
              {(tag) => <Identifier>{ tag() }</Identifier>}
            </Index>
          </span>
        </div>
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

  return (
    <div class="item part"
         classList={{ 'consumed': amount < 0, 'produced': amount > 0 }}
    >
        <span class='decoration'></span>
        <span class='amount' classList={{ 'amount-multiple': Math.abs(amount) > 1 }}>
          { amt(amount) }
        </span>
      <span class='what'><LocalizedIdentifier>{ what }</LocalizedIdentifier></span>
      <Show when={condition_min || condition_max}>
        <span class='condition'>
          { pct(condition_min) } ❤️ { pct(condition_max) }
        </span>
      </Show>
      <Sprite identifier={what} />
    </div>
  );
}


function Sprite(props: { identifier: Game.Identifier } & JSX.HTMLAttributes<HTMLSpanElement>) {
  const [self, rest] = splitProps(props, ["identifier", "class"]);

  return (
    <span
      class={`sprite ${self.class || ''}`}
      {...rest}
      data-sprite={self.identifier}
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
