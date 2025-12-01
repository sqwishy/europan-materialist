import { Switch, Match } from "solid-js";

import { z } from "zod"

import { ResponseDetails } from "./Remote";


const DATETIME_FMT = Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" })
const DATETIME_FMT_LONG = Intl.DateTimeFormat(undefined, { dateStyle: "long", timeStyle: "long" })

export const unixDate = (ts: number): Date => new Date(ts * 1000)
export const pkToDate = (pk: number): Date => new Date(pk / 16)


export const ErrorItems = (params: { title: string, err: any }) => {
	return (
		<>
		<div class="item error stations">
			<span class="no-decoration"></span>
			<span class="what nowrap-rtl" title={params.title}><b>{params.title}</b></span>
		</div>
		<div class="item error">
			<span class="decoration"></span>
			<Switch>
				<Match when={params.err.cause as ResponseDetails}>
					{err =>
						<>
						<span class="comfy">{err().body}</span>
						<span class="smol">{err().code} <b>{err().status}</b></span>
						</>
					}
				</Match>
				<Match when={params.err instanceof z.ZodError}>
					<span class="comfy pre">{z.prettifyError(err() as z.ZodError)}</span>
				</Match>
				<Match when={true}>
					<span class="comfy">{params.err.toString()}</span>
				</Match>
			</Switch>
		</div>
		</>
	)
}


export const Kb = (params : { bytes: number }) => {
  return (params.bytes / 1000).toLocaleString("en", { maximumFractionDigits: 3 }) + 'kb';
}


export const PkTime = (params : { pk: number }) => {
	return <Time time={pkToDate(params.pk)} />
}


export const UnixTime = (params : { unix: number }) => {
	return <Time time={unixDate(params.unix)} />
}


export const Time = (params : { time: Date }) => {
	return (
			<time
				datetime={params.time.toISOString()}
				title={DATETIME_FMT_LONG.format(params.time)}>
				{DATETIME_FMT.format(params.time)}
			</time>
	)
}
