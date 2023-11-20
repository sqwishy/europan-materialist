import { createSignal, createEffect, createContext, createMemo, createResource, useContext, splitProps, JSX } from 'solid-js'
import { A, useSearchParams } from '@solidjs/router'
import { Show, For, Index } from 'solid-js/web'

import * as Data from "./Data"
import stuffUrl from '../assets/stuff.json?url'


const WIKI_BASE_URL = `https://barotraumagame.com/wiki/`;
const TITLE_DEFAULT = document.title;


const amt = (f: number) => f < -1
                         ? Math.abs(f)
                         : f > 1
                         ? f
                         : '';
const pct = (f: number | null) => f === null ? '' : `${100 * f}%`
const unreachable = (n: never): never => n;
// const dbg = v => console.log(v) || v;

const eithers =
  <T,>(a: (_: T) => boolean, b: (_: T) => boolean) =>
  (t: T) => a(t) || b(t)

const boths =
  <T,>(a: (_: T) => boolean, b: (_: T) => boolean) =>
  (t: T) => a(t) && b(t)


type FilterContext = null | "only-consumed" | "only-produced";

const cycleContext =
  (context: FilterContext) => context === null
                            ? "only-consumed"
                            : context === "only-consumed"
                            ? "only-produced"
                            : null

type Filter = {
  substring: string,
  context: FilterContext,
}

type Results = {
  entities: [/* identifier */ Data.Identifier, /* tags */ Data.Identifier[]][],
  processes: Data.Process[],
};


const filterToString =
  (f: Filter): string =>
      f.context === "only-consumed"
    ? `-${f.substring}`
    : f.context === "only-produced"
    ? `*${f.substring}`
    : f.substring


/* enforces case insensitive matching */
const stringToFilter =
  (s: string): Filter =>
      s.startsWith("-")
    ? { context: "only-consumed",
        substring: s.slice(1).toLowerCase() }
    : s.startsWith("*")
    ? { context: "only-produced",
        substring: s.slice(1).toLowerCase() }
    : { context: null,
        substring: s.toLowerCase() }


const resultsLength = (r: Results) => r.entities.length + r.processes.length


type AmountedFilter = (_: { amount: number }) => boolean
type PartFilter = (_: Data.Part) => boolean
type UsedInFilter = (_: Data.Part | Data.WeightedRandomWithReplacement) => boolean
type IdentifierFilter = (_: Data.Identifier) => boolean

const filtersAmount =
  (context : "only-consumed" | "only-produced"): AmountedFilter =>
      context === "only-consumed"
    ? ({ amount }) => amount < 0
    : ({ amount }) => amount > 0


const filtersIdentifier =
  ({ substring, localize }: { substring: string, localize?: Localize }): IdentifierFilter =>
    substring === ""
  ? (_) => true
  :    (identifier) => identifier.includes(substring)
    || (!!localize && localize(identifier).includes(substring))


const filtersPart =
  ({ identifier, amount }: { identifier: IdentifierFilter, amount?: AmountedFilter }) =>
     amount === undefined
  ? (i: Data.Part) => identifier(i.what)
  : (i: Data.Part) => identifier(i.what) && amount(i)


const filtersUsedInProcess =
  ({ part }: { part: PartFilter }) =>
  (i: Data.Part | Data.WeightedRandomWithReplacement): boolean =>
      "what" in i
    ? part(i)
    : i.weighted_random_with_replacement.some(part)


const filtersProcesses =
  ({ identifier, amount, usedIn }:
   { identifier: IdentifierFilter, amount?: AmountedFilter, usedIn: UsedInFilter }) =>
    amount === undefined
  ? (p: Data.Process): boolean => p.uses.some(usedIn)
                               || p.stations.some(identifier)
  : (p: Data.Process): boolean => p.uses.some(usedIn)


const filtersEntities =
  ({ amount, identifier }: { identifier: IdentifierFilter, amount?: AmountedFilter }) =>
    !amount
  ? ([ i, tags ]: [ Data.Identifier, Data.Identifier[] ]) =>
       identifier(i) || tags.some(identifier)
  : (_: any) => false


type Localize = (_: string) => string
const noLocalize: Localize = _ => _
const localizesToLowerWith = (dictionary: Record<string, string>) => (s: string) => dictionary[s]?.toLowerCase() || s
const Locale = createContext<[Localize, Localize]>([noLocalize, noLocalize]);


export const Page = (self: { setTitle: (_: string) => void  }) => {
  const [resource] = createResource(() => Data.fetchStuff(stuffUrl))
  const hasResource = createMemo(() => !resource.loading && !resource.error && resource());

  return (
    <Show when={hasResource()} keyed fallback={<Loading resource={resource} />}>
      {(stuff) => 
        <>
          <footer>
            <p>
              This is a directory of <a href="https://barotraumagame.com/">Barotrauma</a> crafting recipes.
            </p>
            <p>
              Use the <strong>search at the bottom</strong> of the screen or click the words inside braces like <A href="?q=meth" class="identifier">meth</A>.
            </p>
            <p>
              <small>
                This site uses assets and content from Barotrauma.
                It is unaffiliated with <a href="https://undertowgames.com/">Undertow Games</a> or anyone else.
                It is impartial to all clown and husk related matters.
              </small>
            </p>
          </footer>
          <main>
            <Content stuff={stuff} setTitle={self.setTitle} />
          </main>
        </>
      }
    </Show>
  )
}


export const Loading = (self: { resource: any }) => {
  return (
    <>
      <main>
        <div class="loading-screen">
          <Show when={self.resource.loading}>
            loading...
          </Show>
          <Show when={self.resource.error}>
            <strong>failed to load...</strong> {self.resource.error.toString()}
          </Show>
        </div>
      </main>
    </>
  )
}


/* language select, result listing, and search input */
export const Content = (self: { stuff: Data.Stuff, setTitle: (_: string) => void }) => {

  /* fix search bar while mouse is over it to keep it from jumping around  */
  const [getFixedSearch, setFixedSearch] = createSignal<null | number>(null)

  const [getLanguage, setLanguage] = createSignal('English')

  const localize: Localize =
    (text: string) => self.stuff.i18n[getLanguage()]?.[text] || text

  const toEnglish: Localize =
    (text: string) => self.stuff.i18n.English?.[text] || text

  const [searchParams, setSearchParams] = useSearchParams()

  const getSearch = () => searchParams.q?.trim() || ''
  const setSearch = (q: string) => setSearchParams({ q })

  const getLimit = () => parseInt(searchParams.limit, 10) || 50
  const setLimit = (limit: number) => setSearchParams({ limit })

  createEffect(() => self.setTitle(  getSearch()
                                   ? `${getSearch()} — ${TITLE_DEFAULT}`
                                   : TITLE_DEFAULT))

  const getFilter = createMemo((): Filter => stringToFilter(getSearch()))

  const filteredResults = createMemo((): Results => {
    const filter = getFilter();

    /* don't show entities unless there is a substring search */
    /* why can't fucking typescript infer the type for `entities` when this return type is explicit ??? */
    let entities: [string, string[]][] = [];
    let processes = self.stuff.processes

    if (filter.substring.length) {
      const identifier = filtersIdentifier({
        substring: filter.substring,
        localize: getLanguage() in self.stuff.i18n
                ? localizesToLowerWith(self.stuff.i18n[getLanguage()])
                : undefined,
      })
      const amount = filter.context === null
                   ? undefined
                   : filtersAmount(filter.context)
      const part = filtersPart({ amount, identifier })
      const usedIn = filtersUsedInProcess({ part })

      entities = Object.entries(self.stuff.tags_by_identifier)
                       .filter(filtersEntities({ amount, identifier }))
      processes = processes.filter(filtersProcesses({ amount, identifier, usedIn }))
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
    const tags_by_identifier = self.stuff.tags_by_identifier;
    const allTags = Object.values(tags_by_identifier).flat();
    const allIdentifiers = allTags.concat(Object.keys(tags_by_identifier))
    return [...new Set(allIdentifiers)]
  })

  const update = (update: Update) => {
    if ("search" in update)
      setSearch(filterToString({ ...getFilter(), substring: update.search.trim() }))

    else if ("context" in update)
      setSearch(filterToString({ ...getFilter(), context: update.context }))

    else if ("limit" in update)
      setLimit(update.limit)

    else
      unreachable(update)
  };

  return (
    <>
      {/* language select */}
        <p>
          <select
            onchange={(e) => setLanguage(e.currentTarget.value)}
          >
            <option value="">[no localization]</option>
            <For each={Object.entries(self.stuff.i18n).sort()}>
              {([language, dictionary]) => (
                <option
                  value={language}
                  selected={getLanguage()==language}
                >
                  {dictionary[language] || language}
                </option>
              )}
            </For>
          </select>
        </p>

      <hr/>

      {/* items / stuff list */}

      <section>
        <Locale.Provider value={[localize, toEnglish]}>
          <For each={limitedResults().entities}>
            {([identifier, tags]) => <Entity identifier={identifier} tags={tags} />}
          </For>
          <For each={limitedResults().processes}>
            {(p) => <Process process={p} />}
          </For>
        </Locale.Provider>
      </section>

      <p>
        showing <b>{resultsLength(limitedResults())}</b> of <b>{resultsLength(filteredResults())}</b>
        <Show when={resultsLength(limitedResults()) < resultsLength(filteredResults())}>
            <button onclick={() => update({ "limit": resultsLength(limitedResults()) + 100 })}>+100</button>
        </Show>
      </p>

      {/* <p><button onclick={() => window.scrollTo(0, 0)}>surface 🙃</button></p> */}

      <hr/>

      <section
        class="cmd"
        data-fixed={getFixedSearch() !== null ? '' : undefined}
        style={{ top: getFixedSearch() !== null ? `${getFixedSearch()}px` : undefined }}
        onmouseenter={(e) => setFixedSearch(e.target.getBoundingClientRect().top) }
        onmouseleave={() => setFixedSearch(null) }
      >
        <Command
          filter={getFilter()}
          limit={getLimit()}
          update={update} />
      </section>

      <datalist id="cmdcomplete">
        <For each={complete()}>
          {(value) => <option value={value} />}
        </For>
      </datalist>

    </>
  );
};


type Update = { "search": string }
            | { "context": FilterContext }
            | { "limit": number };

function Command(props: { filter: Filter, limit: number, update: (_: Update) => void }) {
  const [self, _] = splitProps(props, ["filter", "limit", "update"]);

  return (
    <>
      {/* substring search */}
      <input
        type="text"
        class="search"
        placeholder="search..."
        accessKey="k"
        list="cmdcomplete"
        value={self.filter.substring}
        onchange={(e) => self.update({ "search": e.currentTarget.value })}
      />
      {/* context filter */}
      <button
        class="context-filter"
        onclick={() => self.update({ "context": cycleContext(self.filter.context) })}
        data-current={self.filter.context}
      >
        <span class="consumed"><span class="decoration"></span></span>
        <span class="produced"><span class="decoration"></span></span>
      </button>
      {/* limit */}
      <input
        id="limit"
        type="text"
        size="6"
        inputmode="decimal"
        placeholder="limit..."
        value={self.limit}
        onchange={(e) => self.update({ "limit": parseInt(e.currentTarget.value, 10) || 0 })}
      />
    </>
  )
}


function Entity({ identifier, tags } : { identifier: Data.Identifier, tags: Data.Identifier[] }) {
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


function Process({ process } : { process: Data.Process }) {
  const { id, skills, time, stations, uses, needs_recipe } = process;
  const [localize] = useContext(Locale);
  return (
    <div class="process" id={id}>
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


function UsesList({ uses } : { uses: (Data.WeightedRandomWithReplacement | Data.Part)[] }) {
    return (
      <For each={uses}>
        {(used) =>
          ("weighted_random_with_replacement" in used)
            ? <WeightedRandom random={used} />
            : <Part part={used} />}
      </For>
    )
}


function Part({ part } : { part: Data.Part }) {
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


function Sprite(props: { identifier: Data.Identifier } & JSX.HTMLAttributes<HTMLSpanElement>) {
  const [self, rest] = splitProps(props, ["identifier", "class"]);

  return (
    <span 
      class={`sprite ${self.class || ''}`}
      {...rest}
      data-sprite={self.identifier}
    >&emsp;</span>
  )
}


function WeightedRandom({ random } : { random: Data.WeightedRandomWithReplacement }) {
  const { weighted_random_with_replacement, amount } = random;
  const [localize] = useContext(Locale);

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
          {(used: Data.Part) => <Part part={used} />}
        </For>
      </ul>
    </>
  )
}


function LocalizedIdentifier({ children } : { children : Data.Identifier | Data.Money }) {
  const [localize, toEnglish] = useContext(Locale);

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


function Identifier({ children } : { children : Data.Identifier }) {
  return <A href={`?q=${children}`} class="identifier">{children}</A>
}
