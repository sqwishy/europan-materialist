import { JSX, splitProps, mergeProps, createSignal, createEffect, createMemo, Accessor } from "solid-js";
import { createStore } from "solid-js/store";

export type UpdateFn<T> = (_: T) => void

export function Toggle(
	props_: {
		value: boolean;
		update?: UpdateFn<boolean>;
		children?: JSX.Element;
		/* no idea why this isn't coming from HTMLButtonElement */
		disabled?: boolean;
	} & JSX.HTMLAttributes<HTMLButtonElement>
) {
	const [props, htmlProps] = splitProps(props_, [
		"value",
		"update",
		"children",
		"class",
		"classList",
		"disabled",
	]);
	return (
		<button
			classList={{
				[props.class || ""]: true,
				"toggle": true,
				"is-checked": props.value == true,
				...props.classList
			}}
			aria-role="checkbox"
			aria-checked={props.value}
			onclick={() => props.update?.(!props.value)}
			disabled={props.disabled}
			{...htmlProps} >
			{props.children || (props.value ? "y" : "n")}
		</button>
	);
}

export function Radio(
	props_: {
		value: boolean;
		update?: UpdateFn<boolean>;
		children?: JSX.Element;
	} & JSX.HTMLAttributes<HTMLButtonElement>
) {
	const [props, htmlProps] = splitProps(props_, [
		"value",
		"update",
		"children",
		"class",
		"classList",
	]);
	return (
		<button
			classList={{
				[props.class || ""]: true,
				"radio": true,
				"is-checked": props.value == true,
				...props.classList
			}}
			aria-role="radio"
			aria-checked={props.value}
			onclick={() => props.update?.(!props.value)}
			{...htmlProps} >
			{props.children}
		</button>
	);
}

export function NumberInput(
	props_: {
		value: number,
		precision?: number,
		update?: UpdateFn<number>,
	} & Omit<JSX.InputHTMLAttributes<HTMLInputElement>, "value" | "onchange">
) {
	const [props, htmlProps] = splitProps(props_, ["value", "update", "precision"])

	const precision = (): number => props.precision ?? 3
	const format = (n: number): string => n.toPrecision(precision())
	const parse =
		(v: string | number) =>
		Number.isFinite(v = Number(v)) ? v : 0

	return (
		<Input
			inputmode="decimal"
			size="5"
			maxlength="12"
			value={format(props.value)}
			{...htmlProps}
			update={props.update
				? (s) => props.update?.(parse(s))
				: undefined}
		/>
	)
}

export function Input(
	props_: {
		value: string,
		update?: UpdateFn<string>,
	} & Omit<JSX.InputHTMLAttributes<HTMLInputElement>, "value" | "onchange">
) {
	const [props, htmlProps] = splitProps(props_, ["value", "update"])

	return (
		<input
			type="text"
			size="12"
			readonly={!props.update}
			value={props.value}
			onchange={(e) => props.update?.(e.target.value)}
			{...htmlProps} />
	)
}

const EmSpace = () => <>&emsp;</>;
