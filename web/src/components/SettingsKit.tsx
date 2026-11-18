// The parts every Settings tab is built from (design "A · Quiet list"): sections separated by
// space alone, rows with the label on the left and the control on the right, one control per
// kind of decision, and a status line under each row that says when a change is saved.
//
//   on/off              → Switch
//   2–4 named options   → Segmented
//   5+ or a long list   → ValueSelect (the native picker, a wheel on iOS)
//   text or a number    → a field that saves on Enter, or on blur when it changed; Esc reverts
//   a whole file        → FileEditor, with an explicit Save

import {
  type KeyboardEvent,
  type MutableRefObject,
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import { reasonText } from "../lib/providers";
import { Icon, Spinner } from "./Icon";
import { Button, fieldClass, inputClass } from "./Modal";

export type Settings = Record<string, unknown>;
/** Merges a partial settings document into settings.json; `null` removes a key. */
export type Patch = (value: Settings) => Promise<void>;

/** `nest("a.b", 1)` is `{ a: { b: 1 } }`, the shape update_settings merges. */
export function nest(path: string, value: unknown): Settings {
  return path.split(".").reduceRight<unknown>((acc, key) => ({ [key]: acc }), value) as Settings;
}

/** A dotted settings key, or how to turn the new value into a patch. */
export type SaveAs<T> = string | ((value: T) => Settings);

function toPatch<T>(saveAs: SaveAs<T>, value: T): Settings {
  return typeof saveAs === "string" ? nest(saveAs, value) : saveAs(value);
}

// ---------------------------------------------------------------------------------------------
// Save status

export type SaveStatus =
  | { kind: "idle" }
  | { kind: "saving" }
  | { kind: "saved" }
  /** The save was sent and failed. */
  | { kind: "error"; message: string }
  /** The input was rejected before anything was sent. */
  | { kind: "invalid"; message: string };

const IDLE: SaveStatus = { kind: "idle" };
/** "Saving…" appears only for saves slower than this, so quick ones don't flicker. */
export const SLOW_SAVE_MS = 400;
/** How long "Saved" stays. */
export const SAVED_MS = 2000;

function clearAll(timers: MutableRefObject<number[]>): void {
  for (const timer of timers.current) window.clearTimeout(timer);
  timers.current = [];
}

/** Tracks one row's save: Saving… after a moment, then Saved for two seconds, or the error. */
export function useSaveStatus(): {
  status: SaveStatus;
  track: (work: () => Promise<unknown>) => Promise<boolean>;
  invalid: (message: string) => void;
  reset: () => void;
} {
  const [status, setStatus] = useState<SaveStatus>(IDLE);
  const timers = useRef<number[]>([]);
  useEffect(() => () => clearAll(timers), []);
  const track = useCallback(async (work: () => Promise<unknown>) => {
    clearAll(timers);
    setStatus(IDLE);
    timers.current.push(window.setTimeout(() => setStatus({ kind: "saving" }), SLOW_SAVE_MS));
    try {
      await work();
      clearAll(timers);
      setStatus({ kind: "saved" });
      timers.current.push(window.setTimeout(() => setStatus(IDLE), SAVED_MS));
      return true;
    } catch (error) {
      clearAll(timers);
      setStatus({ kind: "error", message: reasonText(error) });
      return false;
    }
  }, []);
  const invalid = useCallback((message: string) => {
    clearAll(timers);
    setStatus({ kind: "invalid", message });
  }, []);
  const reset = useCallback(() => {
    clearAll(timers);
    setStatus(IDLE);
  }, []);
  return { status, track, invalid, reset };
}

/** The status line. It is always rendered, so screen readers announce what appears in it. */
export function StatusText({ status, id }: { status: SaveStatus; id?: string }) {
  let content: ReactNode = null;
  if (status.kind === "saving")
    content = (
      <span className="inline-flex items-center gap-1.5 text-fg-muted">
        <span aria-hidden="true" className="inline-flex">
          <Spinner />
        </span>
        Saving…
      </span>
    );
  else if (status.kind === "saved")
    content = (
      <span className="inline-flex items-center gap-1 text-add-fg">
        <Icon name="check" size={12} />
        Saved
      </span>
    );
  else if (status.kind === "error" || status.kind === "invalid")
    content = (
      <span className="inline-flex items-baseline gap-1 text-danger">
        <Icon name="alert" size={12} className="self-center" />
        <span className="[overflow-wrap:anywhere]">
          {status.kind === "error" ? `Couldn't save. ${status.message}` : status.message}
        </span>
      </span>
    );
  return (
    <span id={id} role="status" className="text-[11px] [&:not(:empty)]:mt-0.5">
      {content}
    </span>
  );
}

// ---------------------------------------------------------------------------------------------
// Layout

/** A group of rows: a quiet heading, an optional intro, then the rows. No border, no fill. */
export function Section({
  title,
  intro,
  children,
}: {
  title?: string;
  intro?: ReactNode;
  children: ReactNode;
}) {
  const headingId = useId();
  return (
    <section aria-labelledby={title ? headingId : undefined} className="mt-6 first:mt-0">
      {title ? (
        <h3 id={headingId} className="text-[12px] font-semibold text-fg-muted">
          {title}
        </h3>
      ) : null}
      {intro ? (
        <p className="mt-0.5 mb-1 text-[12px] text-fg-muted [overflow-wrap:anywhere]">{intro}</p>
      ) : null}
      {children}
    </section>
  );
}

export interface RowIds {
  control: string;
  label: string;
  hint: string;
}

export function useRowIds(): RowIds {
  const id = useId();
  return { control: `${id}-control`, label: `${id}-label`, hint: `${id}-hint` };
}

/**
 * How a row places its control. `inline`: always to the right of the label. `stacked`: always
 * below it, full width. `wide`: to the right on desktop, below it on phones (segmented controls).
 */
export type RowLayout = "inline" | "stacked" | "wide";

const ROW_LAYOUT: Record<RowLayout, { row: string; control: string }> = {
  inline: {
    row: "flex min-h-[42px] items-center gap-4 py-1.5 pointer-coarse:min-h-11",
    control: "shrink-0",
  },
  stacked: { row: "flex flex-col gap-1.5 py-1.5", control: "min-w-0" },
  wide: {
    row: "flex min-h-[42px] items-center gap-4 py-1.5 pointer-coarse:min-h-11 max-sm:flex-col max-sm:items-stretch max-sm:gap-1.5",
    control: "shrink-0 max-sm:w-full",
  },
};

/**
 * One setting: label, hint and status on the left, the control on the right (or below). When
 * `labelTarget` is set the text is a <label> for the control, so tapping it works the control.
 */
export function Row({
  ids,
  label,
  hint,
  status,
  disabled = false,
  layout = "inline",
  labelTarget = false,
  children,
}: {
  ids: RowIds;
  label: ReactNode;
  hint?: ReactNode;
  status: SaveStatus;
  disabled?: boolean;
  layout?: RowLayout;
  labelTarget?: boolean;
  children: ReactNode;
}) {
  const classes = ROW_LAYOUT[layout];
  const text = (
    <>
      <span id={ids.label} className={`text-[13px] ${disabled ? "text-fg-muted" : "text-fg"}`}>
        {label}
      </span>
      {hint ? (
        <span id={ids.hint} className="text-[11px] text-fg-muted [overflow-wrap:anywhere]">
          {hint}
        </span>
      ) : null}
      <StatusText status={status} />
    </>
  );
  const textClass = `flex min-w-0 flex-col ${layout === "stacked" ? "" : "flex-1"}`;
  return (
    <div className={classes.row}>
      {labelTarget ? (
        <label htmlFor={ids.control} className={textClass}>
          {text}
        </label>
      ) : (
        <div className={textClass}>{text}</div>
      )}
      <div className={classes.control}>{children}</div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------------
// Controls

/** An on/off switch: 40×24, the accent track when on, an outlined track when off. */
export function Switch({
  id,
  checked,
  onChange,
  disabled = false,
  labelledBy,
  describedBy,
}: {
  id?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  labelledBy?: string;
  describedBy?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      id={id}
      aria-checked={checked}
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={`flex h-[18px] w-8 shrink-0 items-center rounded-full p-[2px] transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${checked ? "bg-accent" : "shadow-[inset_0_0_0_1.5px_var(--fg-muted)]"}`}
    >
      <span
        aria-hidden="true"
        className={`size-[14px] rounded-full transition-transform ${checked ? "translate-x-3.5 bg-accent-fg" : "bg-fg-muted"}`}
      />
    </button>
  );
}

export interface Choice<T extends string> {
  value: T;
  label: string;
}

/**
 * A segmented control for 2–4 named options. It is a group of native radio buttons, so arrow
 * keys move and select and the group is one tab stop; the labels are the segments.
 */
export function Segmented<T extends string>({
  choices,
  value,
  onChange,
  labelledBy,
  label,
  describedBy,
  disabled = false,
  className = "",
}: {
  choices: Choice<T>[];
  value: T;
  onChange: (value: T) => void;
  labelledBy?: string;
  /** An accessible name, when no visible label names the group. */
  label?: string;
  describedBy?: string;
  disabled?: boolean;
  className?: string;
}) {
  const name = useId();
  return (
    <fieldset
      aria-labelledby={labelledBy}
      aria-label={label}
      aria-describedby={describedBy}
      disabled={disabled}
      className={`inline-flex h-[30px] min-w-0 gap-0.5 rounded-md bg-muted p-0.5 pointer-coarse:h-9 ${className}`}
    >
      {choices.map((choice) => {
        const selected = choice.value === value;
        return (
          <label
            key={choice.value}
            className={`flex flex-1 cursor-pointer items-center justify-center whitespace-nowrap rounded px-2.5 text-[13px] has-[:disabled]:cursor-not-allowed has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-accent has-[:focus-visible]:outline-solid ${selected ? "bg-raised font-medium text-fg shadow-raised" : "text-fg-muted hover:text-fg"}`}
          >
            <input
              type="radio"
              name={name}
              value={choice.value}
              checked={selected}
              onChange={() => onChange(choice.value)}
              className="sr-only"
            />
            {choice.label}
          </label>
        );
      })}
    </fieldset>
  );
}

/**
 * A select drawn as its value with an up/down chevron, right-aligned and without a box. It is a
 * native <select>, so phones show their own picker.
 */
export function ValueSelect({
  id,
  value,
  onChange,
  disabled = false,
  labelledBy,
  describedBy,
  children,
}: {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  labelledBy?: string;
  describedBy?: string;
  children: ReactNode;
}) {
  return (
    <span className="relative inline-flex max-w-full items-center">
      <select
        id={id}
        value={value}
        disabled={disabled}
        aria-labelledby={labelledBy}
        aria-describedby={describedBy}
        onChange={(event) => onChange(event.target.value)}
        className="h-[30px] max-w-64 cursor-pointer appearance-none truncate rounded-md bg-transparent py-0 pr-6 pl-2 text-right text-[13px] text-fg-muted [text-align-last:right] hover:bg-hover disabled:cursor-not-allowed pointer-coarse:h-9 pointer-coarse:text-base max-sm:max-w-44"
      >
        {children}
      </select>
      <Icon
        name="chevronUpDown"
        size={12}
        className="pointer-events-none absolute right-1.5 text-fg-muted"
      />
    </span>
  );
}

type CommitResult = "saved" | "invalid" | "failed";

/**
 * A text field (or textarea) that saves on Enter, or on blur when it changed, and reverts on Esc.
 * While it has unsaved text it carries `data-dirty`, so the dialog keeps Esc for the field.
 */
function CommitField({
  id,
  value,
  onCommit,
  className,
  multiline = false,
  placeholder,
  disabled = false,
  numeric = false,
  invalid = false,
  labelledBy,
  describedBy,
  onRevert,
}: {
  id: string;
  /** The saved value. */
  value: string;
  onCommit: (text: string) => Promise<CommitResult>;
  className: string;
  multiline?: boolean;
  placeholder?: string;
  disabled?: boolean;
  numeric?: boolean;
  invalid?: boolean;
  labelledBy: string;
  describedBy?: string;
  onRevert: () => void;
}) {
  const [text, setText] = useState(value);
  // A new saved value (this field's or a reload) replaces what is shown.
  useEffect(() => setText(value), [value]);
  const dirty = text !== value;
  const commit = async () => {
    if (!dirty) return;
    const result = await onCommit(text);
    if (result === "failed") setText(value);
  };
  const onKeyDown = (event: KeyboardEvent<HTMLInputElement | HTMLTextAreaElement>) => {
    if (event.key === "Escape" && dirty) {
      setText(value);
      onRevert();
    } else if (event.key === "Enter" && !multiline && !event.nativeEvent.isComposing) {
      event.preventDefault();
      void commit();
    }
  };
  const shared = {
    id,
    value: text,
    placeholder,
    disabled,
    className,
    "aria-invalid": invalid || undefined,
    "aria-labelledby": labelledBy,
    "aria-describedby": describedBy,
    "data-dirty": dirty || undefined,
    autoCapitalize: "none",
    autoCorrect: "off",
    spellCheck: false,
    onKeyDown,
    onBlur: () => void commit(),
  } as const;
  return multiline ? (
    <textarea {...shared} onChange={(event) => setText(event.target.value)} />
  ) : (
    <input
      {...shared}
      inputMode={numeric ? "numeric" : undefined}
      enterKeyHint="done"
      onChange={(event) => setText(event.target.value)}
    />
  );
}

// ---------------------------------------------------------------------------------------------
// Settings rows: a control bound to settings.json, with its own status line

interface SettingRowProps {
  label: ReactNode;
  hint?: ReactNode;
  patch: Patch;
  disabled?: boolean;
}

/** Shows the new value at once, and the saved one again if saving fails. */
function useOptimistic<T>(saved: T, patch: Patch) {
  const { status, track } = useSaveStatus();
  const [pending, setPending] = useState<{ value: T } | null>(null);
  const change = async (next: T, value: Settings) => {
    setPending({ value: next });
    await track(() => patch(value));
    setPending(null);
  };
  return { shown: pending ? pending.value : saved, status, change };
}

export function SwitchSetting({
  label,
  hint,
  value,
  saveAs,
  patch,
  disabled = false,
}: SettingRowProps & { value: boolean; saveAs: SaveAs<boolean> }) {
  const ids = useRowIds();
  const { shown, status, change } = useOptimistic(value, patch);
  return (
    <Row ids={ids} label={label} hint={hint} status={status} disabled={disabled} labelTarget>
      <Switch
        id={ids.control}
        checked={shown}
        disabled={disabled}
        labelledBy={ids.label}
        describedBy={hint ? ids.hint : undefined}
        onChange={(next) => void change(next, toPatch(saveAs, next))}
      />
    </Row>
  );
}

export function ChoiceSetting<T extends string>({
  label,
  hint,
  value,
  choices,
  saveAs,
  patch,
  disabled = false,
}: SettingRowProps & { value: T; choices: Choice<T>[]; saveAs: SaveAs<T> }) {
  const ids = useRowIds();
  const { shown, status, change } = useOptimistic(value, patch);
  return (
    <Row ids={ids} label={label} hint={hint} status={status} disabled={disabled} layout="wide">
      <Segmented
        choices={choices}
        value={shown}
        disabled={disabled}
        labelledBy={ids.label}
        describedBy={hint ? ids.hint : undefined}
        onChange={(next) => void change(next, toPatch(saveAs, next))}
        className="max-sm:flex max-sm:w-full"
      />
    </Row>
  );
}

export function SelectSetting({
  label,
  hint,
  value,
  saveAs,
  patch,
  disabled = false,
  children,
}: SettingRowProps & { value: string; saveAs: SaveAs<string>; children: ReactNode }) {
  const ids = useRowIds();
  const { shown, status, change } = useOptimistic(value, patch);
  return (
    <Row ids={ids} label={label} hint={hint} status={status} disabled={disabled} labelTarget>
      <ValueSelect
        id={ids.control}
        value={shown}
        disabled={disabled}
        labelledBy={ids.label}
        describedBy={hint ? ids.hint : undefined}
        onChange={(next) => void change(next, toPatch(saveAs, next))}
      >
        {children}
      </ValueSelect>
    </Row>
  );
}

/** A path or other text. Clearing it removes the key, so the default applies again. */
export function TextSetting({
  label,
  hint,
  value,
  saveAs,
  patch,
  placeholder,
  multiline = false,
  disabled = false,
}: SettingRowProps & {
  value: string;
  saveAs: SaveAs<string | null>;
  placeholder?: string;
  multiline?: boolean;
}) {
  const ids = useRowIds();
  const { status, track, reset } = useSaveStatus();
  const commit = async (text: string): Promise<CommitResult> => {
    const trimmed = text.trim();
    const ok = await track(() => patch(toPatch(saveAs, trimmed === "" ? null : trimmed)));
    return ok ? "saved" : "failed";
  };
  return (
    <Row
      ids={ids}
      label={label}
      hint={hint}
      status={status}
      disabled={disabled}
      layout="stacked"
      labelTarget
    >
      <CommitField
        id={ids.control}
        value={value}
        onCommit={commit}
        onRevert={reset}
        placeholder={placeholder}
        multiline={multiline}
        disabled={disabled}
        labelledBy={ids.label}
        describedBy={hint ? ids.hint : undefined}
        className={`${inputClass} font-mono ${multiline ? "h-24" : ""}`}
      />
    </Row>
  );
}

/** A whole number with its unit. Clearing it removes the key, so the default applies again. */
export function NumberSetting({
  label,
  hint,
  value,
  saveAs,
  patch,
  unit,
  disabled = false,
}: SettingRowProps & { value: string; saveAs: SaveAs<number | null>; unit: string }) {
  const ids = useRowIds();
  const unitId = `${ids.control}-unit`;
  const { status, track, invalid, reset } = useSaveStatus();
  const commit = async (text: string): Promise<CommitResult> => {
    const trimmed = text.trim();
    if (trimmed !== "" && !/^\d+$/.test(trimmed)) {
      invalid("Enter a whole number.");
      return "invalid";
    }
    const ok = await track(() =>
      patch(toPatch(saveAs, trimmed === "" ? null : Number.parseInt(trimmed, 10))),
    );
    return ok ? "saved" : "failed";
  };
  const describedBy = [unitId, hint ? ids.hint : null].filter(Boolean).join(" ");
  return (
    <Row ids={ids} label={label} hint={hint} status={status} disabled={disabled} labelTarget>
      <span className="flex items-center gap-2">
        <CommitField
          id={ids.control}
          value={value}
          onCommit={commit}
          onRevert={reset}
          numeric
          disabled={disabled}
          invalid={status.kind === "invalid"}
          labelledBy={ids.label}
          describedBy={describedBy}
          className={`${fieldClass} w-24 font-mono`}
        />
        {/* A fixed width, so fields with different units line up. */}
        <span id={unitId} className="min-w-10 text-[12px] text-fg-muted">
          {unit}
        </span>
      </span>
    </Row>
  );
}

/**
 * A whole file edited as text, with an explicit Save and Revert. Everything else in Settings
 * saves on its own; this says "Unsaved changes" until it is saved.
 */
export function FileEditor({
  label,
  initial,
  onSave,
  saveLabel = "Save",
  heightClass = "h-48",
}: {
  /** The accessible name of the text area. */
  label: string;
  initial: string;
  onSave: (text: string) => Promise<void>;
  saveLabel?: string;
  heightClass?: string;
}) {
  const id = useId();
  const [text, setText] = useState(initial);
  const [busy, setBusy] = useState(false);
  const { status, track, reset } = useSaveStatus();
  useEffect(() => setText(initial), [initial]);
  const dirty = text !== initial;
  const save = async () => {
    setBusy(true);
    await track(() => onSave(text));
    setBusy(false);
  };
  return (
    <div className="flex flex-col gap-2 py-1.5">
      <textarea
        id={id}
        aria-label={label}
        className={`${inputClass} ${heightClass} font-mono text-[12px]`}
        value={text}
        spellCheck={false}
        autoCapitalize="none"
        autoCorrect="off"
        onChange={(event) => {
          setText(event.target.value);
          if (status.kind !== "idle") reset();
        }}
      />
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Button variant="primary" disabled={!dirty || busy} onClick={() => void save()}>
          {busy ? "Saving…" : saveLabel}
        </Button>
        <Button
          disabled={!dirty || busy}
          onClick={() => {
            setText(initial);
            reset();
          }}
        >
          Revert
        </Button>
        {dirty && status.kind === "idle" ? (
          <span className="ml-1 text-[12px] text-fg-muted">Unsaved changes</span>
        ) : null}
        <span className="ml-1">
          <StatusText status={status} />
        </span>
      </div>
    </div>
  );
}

/**
 * An inline confirmation for something that can't be undone: the question, what happens next,
 * a filled danger button and Cancel (focused, so Enter doesn't destroy anything by accident).
 * Only attention content like this gets a fill inside the dialog, and never a border.
 */
export function InlineConfirm({
  question,
  consequence,
  confirmLabel,
  busyLabel,
  failure,
  onConfirm,
  onCancel,
}: {
  question: string;
  consequence?: ReactNode;
  confirmLabel: string;
  busyLabel: string;
  /** Starts the error line, before the reason: "Couldn't remove the package." */
  failure: string;
  onConfirm: () => Promise<void>;
  onCancel: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const questionId = useId();
  const box = useRef<HTMLFieldSetElement>(null);
  useEffect(() => box.current?.querySelector<HTMLElement>("[data-autofocus]")?.focus(), []);
  const confirm = async () => {
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
    } catch (err) {
      setError(reasonText(err));
      setBusy(false);
    }
  };
  return (
    <fieldset
      ref={box}
      aria-labelledby={questionId}
      className="min-w-0 space-y-2 rounded-md bg-del-bg p-3 text-[13px] text-fg"
    >
      <p id={questionId} className="font-semibold [overflow-wrap:anywhere]">
        {question}
      </p>
      {consequence ? <p>{consequence}</p> : null}
      {error ? (
        <p role="alert" className="flex items-baseline gap-1.5">
          <Icon name="alert" size={12} className="shrink-0 self-center text-danger" />
          <span className="[overflow-wrap:anywhere]">
            {failure} {error}
          </span>
        </p>
      ) : null}
      <div className="flex gap-2 max-sm:flex-col">
        <Button variant="dangerFill" disabled={busy} onClick={() => void confirm()}>
          {busy ? busyLabel : confirmLabel}
        </Button>
        <Button data-autofocus disabled={busy} onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </fieldset>
  );
}
