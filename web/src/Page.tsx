import { createSignal, createContext, createEffect, createMemo, createResource, useContext, splitProps, JSX } from 'solid-js'
import { createStore } from 'solid-js/store'
import { Show, For } from 'solid-js/web'
import * as Data from "./Data"

const amt = (f: number) => f < -1
                         ? Math.abs(f)
                         : f > 1
                         ? f
                         : '';


const pct = (f: number | null) => f === null ? '' : `${100 * f}%`


// const dbg = v => console.log(v) || v;


// substring part name?
type Filter = string;


const applyFilter = (p: Data.Process, f: Filter) => (
     p.uses.some(i => "what" in i && i.what.includes(f))
  || p.stations.some(s => s.includes(f)));


type Localize = (_: string) => string
const Locale = createContext<[Localize]>([_ => _]);


export const Page = () => {
  const [resource] = createResource(Data.fetchStuff)
  const [getSearch, setSearch] = createSignal<Filter>('')
  const [getLimit, setLimit] = createSignal<number>(20)
  const [getLanguage, setLanguage] = createSignal('English')

  const [processes, setProcesses] = createStore<Data.Process[]>([]);

  createEffect(() => {
    let processes, search: Filter, limit: number;

    if (   resource.loading
        || resource.error
        || !(processes = resource.latest?.processes))
      return setProcesses([]);

    if ((search = getSearch()).length)
      processes = processes.filter((p) => applyFilter(p, search))

    if (limit = getLimit())
      processes = processes.slice(0, limit)

    /* maybe using a store for this is silly,
     * thought reconcile might be good but seems to not work */
    setProcesses(processes)
  });

  const items = createMemo(() => {
    if (   resource.loading
        || resource.error
        || !resource.latest)
      return [];

    let search: Filter;
    let limit: number;
    let items = Object.entries(resource.latest.tags_by_identifier);
    const _filterFn =
      ([identifier, tags]: [string /*Data.Identifier*/, Data.Identifier[]]) => identifier.includes(search)
                           || tags.some(t => t.includes(search));


    if ((search = getSearch()).length)
      items = items.filter(_filterFn)

    if (limit = getLimit())
      items = items.slice(0, limit)

    return items
  })

  const complete = createMemo(() => {
    if (   resource.loading
        || resource.error
        || !resource.latest)
      return [];

    const { tags_by_identifier } = resource.latest;
    const allTags = Object.values(tags_by_identifier).flat();
    const allIdentifiers = allTags.concat(Object.keys(tags_by_identifier))
    return [...new Set(allIdentifiers)]
  })

  const localize: Localize = (text: string) => (   !resource.loading
                                                && !resource.error
                                                && (resource()?.i18n[getLanguage()] || {})[text] || text)

  return (
    <>
      {/* language select */}
      <Show when={!resource.loading && !resource.error && resource()} keyed>
        {(stuff: Data.Stuff) => (
          <p>
            <select
              onchange={(e) => setLanguage(e.currentTarget.value)}
            >
              <option value="">[no localization]</option>
              <For each={Object.entries(stuff.i18n).sort()}>
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
        )}
      </Show>
      {/* search / filter */}
      <p>
        <input
          id="search"
          type="text"
          size="30"
          placeholder="search..."
          value={getSearch()}
          onchange={(e) => setSearch(e.currentTarget.value)}
        />
        {getSearch()}
      </p>
      <p>
        limit <input
          id="limit"
          type="text"
          size="6"
          inputmode="decimal"
          placeholder="limit..."
          value={getLimit()}
          onchange={(e) => setLimit(parseInt(e.currentTarget.value, 10) || 0)}
        />
        showing {processes?.length}
      </p>
      {/* stuff */}
      <Show when={resource.error} keyed>
        {({ message }) => <p>error: {message}</p>}
      </Show>
      <Show when={resource.loading}>
        <p>loading</p>
      </Show>
      <Locale.Provider value={[localize]}>
        <For each={getSearch() ? items() : []}>
          {([identifier, tags]) => <Entity identifier={identifier} tags={tags} />}
        </For>
        <For each={processes}>
          {(proc) => <Process proc={proc} />}
        </For>
      </Locale.Provider>

      <p><button onclick={() => window.scrollTo(0, 0)}>surface 🙃</button></p>

      <Command update={setSearch} />

      <datalist id="cmdcomplete">
        <For each={complete()}>
          {(value) => <option value={value} />}
        </For>
      </datalist>

      <footer>
        <p>This site uses content and graphics from <a href="https://barotraumagame.com/">Barotrauma</a>, property of <a href="https://undertowgames.com/">Undertow Games</a>. It is not endorsed, affiliated, or conspiring with Undertow Games.</p>
      </footer>
    </>
  );
};


type Update = string;

function Command(props: { update: (_: Update) => void }){
  const [self, _] = splitProps(props, ["update"]);
  return (
    <div class="cmd">
      <input
        id="cmdline"
        accessKey="k"
        class="cmdline"
        list="cmdcomplete"
        onchange={(e) => self.update(e.currentTarget.value)}
      />
    </div>
  )
}


function Entity({ identifier, tags } : { identifier: Data.Identifier, tags: Data.Identifier[] }) {
  return (
      <div class="entity">
        <div class="item">
          <span class="decoration"></span>
          <span class="what"><Localized>{ identifier }</Localized></span>
          <Sprite what={identifier} />
        </div>
        <div class="item">
          <span class="decoration"/>
          <span class="taglist">
            🏷️
            <For each={tags}>
              {(tag) => <Identifier>{ tag }</Identifier>}
            </For>
          </span>
        </div>
      </div>
  )
}

function Process({ proc } : { proc: Data.Process }) {
  const { time, stations, uses, needs_recipe, description } = proc;
  // const [station, ...otherStations] = stations;
  return (
    <div class="process">
      {/* parts consumed */}
      <UsesList uses={uses.filter(({ amount }) => amount < 0)} />

      {/* station and time */} 
      <div class="item stations">
        <span class="time">
          <Show when={time}>
            ⏱️ {proc.time}s
          </Show>
        </span>
        <For each={stations.slice(0,1)}>
          {(station) => (
            <>
              <span class="station"><Localized>{station}</Localized></span>
              <Sprite what={station}/>
            </>
          )}
        </For>
      </div>
      <For each={stations.slice(1)}>
        {(station) => (
          <div class="item stations">
            <span class="time"></span>
            <span class="station"><Localized>{station}</Localized></span>
            <Sprite what={station}/>
          </div>
        )}
      </For>

      {/* parts produced */}
      <UsesList uses={uses.filter(({ amount }) => amount >= 0)} />

      <Show when={needs_recipe || description}>
        <span class="sub">
          <Show when={needs_recipe}>
            <span>🧠 requires recipe</span>
          </Show>
          <Show when={description}>
            <span>👉 {description}</span>
          </Show>
        </span>
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
      <span class='what'><Localized>{ what }</Localized></span>
      <Show when={condition_min || condition_max}>
        <span class='condition'>
          { pct(condition_min) } ❤️ { pct(condition_max) }
        </span>
      </Show>
      <Sprite what={what} />
    </div>
  );
}


function Sprite(props: { what: Data.Identifier } & JSX.HTMLAttributes<HTMLSpanElement>) {
  const [self, rest] = splitProps(props, ["what", "class"]);
  return (
    <span class={`sprite ${self.class || ''}`} {...rest} data-sprite={self.what}>&emsp;</span>
  )
}

function WeightedRandom({ random } : { random: Data.WeightedRandomWithReplacement }) {
  const { weighted_random_with_replacement, amount } = random;
  return (
    <>
      <div class="item part random"
           classList={{ 'consumed': amount < 0, 'produced': amount > 0 }}
      >
        <span class='decoration'></span>
        <span class='amount'>{ amount }</span>
        <span class='what'>🎲 random</span>
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

function Localized({ children } : { children : Data.Identifier | Data.Money }) {
  const [localize] = useContext(Locale);
  return <>{localize(children)} <Identifier>{children}</Identifier></>
}

function Identifier({ children } : { children : Data.Identifier }) {
  return <a href="#" class="identifier">{children}</a>
}
