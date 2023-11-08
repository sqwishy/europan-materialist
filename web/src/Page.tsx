import { createSignal, createContext, createResource, useContext, splitProps, JSX } from 'solid-js'
import { Show, For } from 'solid-js/web'
import * as Data from "./Data"

const amt = (f: number) => f < -1
                         ? Math.abs(f)
                         : f > 1
                         ? f
                         : '';


const pct = (f: number | null) => f === null ? '' : `${100 * f}%`


const dbg = v => console.log(v) || v;


// substring part name?
type Filter = string;


const applyFilter = (p: Data.Process, f: Filter) => (
     p.uses.some(i => "what" in i && i.what.includes(f))
  || p.stations.some(s => s.includes(f)));


type Localize = (_: string) => string
const Locale = createContext<[Localize]>([_ => _]);


type GetSprite = (_: string) => string | null
const Sprites = createContext<[GetSprite]>([_ => null]);


export const Page = () => {
  const [resource] = createResource(Data.fetchStuff)
  const [getSearch, setSearch] = createSignal('')
  const [getLanguage, setLanguage] = createSignal('English')

  const localize: Localize = (text: string) => (   !resource.loading
                                                && !resource.error
                                                && (resource()?.i18n[getLanguage()] || {})[text] || text)
  const getSprite: GetSprite = (i: string) => (   !resource.loading
                                               && !resource.error
                                               && (resource()?.sprites[i]) || null)

  const filterProcs = (procs: Data.Process[], filter: Filter) => {
    if (!filter.length)
      return procs

    return procs.filter((p) => applyFilter(p, filter))
  }

  return (
    <>
      {/* language select */}
      <Show when={!resource.loading && !resource.error && resource()} keyed>
        {(stuff: Data.Stuff) => (
          <div>
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
            {getLanguage()}
          </div>
        )}
      </Show>
      {/* search / filter */}
      <div>
        <input
          id="search"
          type="text"
          placeholder="search..."
          value={getSearch()}
          onchange={(e) => setSearch(e.currentTarget.value)}
        />
        {getSearch()}
      </div>
      {/* stuff */}
      <Show when={resource.error} keyed>
        {({ message }) => <p>error: {message}</p>}
      </Show>
      <Show when={resource.loading}>
        <p>loading</p>
      </Show>
      <Sprites.Provider value={[getSprite]}>
        <Locale.Provider value={[localize]}>
          <Show when={!resource.loading && !resource.error && resource()} keyed>
            {(stuff: Data.Stuff) => (
              <For each={filterProcs(stuff.procs, getSearch())/*.filter(({stations}) => stations.length > 1)*/}>
                {(proc) => <Process proc={proc} />}
              </For>
            )}
          </Show>
        </Locale.Provider>
      </Sprites.Provider>
    </>
  );
};


function Process({ proc } : { proc: Data.Process }) {
  const { time, stations, uses, needs_recipe, description } = proc;
  return (
    <div class="process">
      {/* parts consumed */}
      <UsesList uses={uses.filter(({ amount }) => amount < 0)} />

      {/* station and time */} 
      <div class="item stations">
        <Show when={time}>
          <span class="time">⏱️ {proc.time}s</span>
        </Show>
        <For each={stations}>
          {(station) => <><span class="station">{station}</span><Sprite what={station}/></>}
        </For>
      </div>

      {/* parts produced */}
      <UsesList uses={uses.filter(({ amount }) => amount >= 0)} />

      <Show when={needs_recipe || description}>
        <span class="sub">
          <Show when={needs_recipe}>
            <span>🧠 requires recipe</span>
          </Show>
          <Show when={description}>
            <span>🫘 {description}</span>
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
  const [localize] = useContext(Locale)
  const [sprite] = useContext(Sprites)
  return (
    <div class="item part"
         classList={{ 'consumed': amount < 0, 'produced': amount > 0 }}
    >
      <span class='decoration'></span>
      <span class='amount'
        classList={{ 'amount-multiple': Math.abs(amount) > 1 }}
      >{ amt(amount) }</span>
      <span class='what'>{ localize(what) }</span>
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
  const [sprite] = useContext(Sprites)
  return (
    <Show when={sprite(self.what)} keyed>
      {(data) => <span class={`sprite ${self.class || ''}`} {...rest}>
        <img src={`data:image/webp;base64,${data}`}/>
      </span>}
    </Show>
  )
}


function WeightedRandom({ random } : { random: Data.WeightedRandomWithReplacement }) {
  const { weighted_random_with_replacement, amount } = random;
  return (
    <>
      <div class="item part random"
           classList={{ 'consumed': amount < 0, 'produced': amount > 0 }}>
        <span class='amount'>{ amount }</span>
        <span class='what'>chosen at random</span>
      </div>
      <For each={weighted_random_with_replacement}>
        {(used: Data.Part) => <Part part={used} />}
      </For>
    </>
  )
}
