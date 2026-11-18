import * as Popover from "@radix-ui/react-popover";
import { Command } from "cmdk";
import { useEffect, useMemo, useState } from "react";
import { COARSE_POINTER, useMediaQuery } from "../lib/media";
import { modelKey, recentModels, rememberModel, shortModelName } from "../lib/models";
import { shortcutLabel } from "../lib/native";
import type { ModelInfo } from "../protocol/types";
import { refreshProviders, reportError, request, toast, ui, useApp } from "../state/store";
import { Icon } from "./Icon";

const chipClass =
  "flex min-w-0 items-center gap-1 rounded-md px-2 py-1 text-[12px] text-fg-muted hover:bg-hover hover:text-fg pointer-coarse:min-h-11";

/** Lists longer than this get Current and Recent groups at the top. */
const PIN_AFTER = 10;
const groupClass =
  "text-[11px] text-fg-faint [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:pb-1 [&_[cmdk-group-heading]]:pt-2";
const modelRow =
  "flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-[13px] text-fg data-[selected=true]:bg-hover pointer-coarse:min-h-11";

/**
 * Searchable model list: Current and Recent first (in long lists, while not searching), then
 * each provider.
 * Arrow keys and Enter work from the search field; `autoFocus` is off on touch so opening it
 * doesn't raise the keyboard.
 */
export function ModelList({
  sessionId,
  current,
  onDone,
  autoFocus = true,
}: {
  sessionId: string;
  current: ModelInfo | null;
  onDone: () => void;
  autoFocus?: boolean;
}) {
  const [models, setModels] = useState<ModelInfo[] | null>(null);
  const [query, setQuery] = useState("");
  const providers = useApp((s) => s.providers?.providers ?? null);
  useEffect(() => {
    request<ModelInfo[]>("get_available_models", { sessionId })
      .then(setModels)
      .catch((error) => {
        setModels([]);
        reportError(error);
      });
  }, [sessionId]);
  useEffect(() => {
    if (!providers) void refreshProviders();
  }, [providers]);

  const byKey = useMemo(() => new Map((models ?? []).map((m) => [modelKey(m), m])), [models]);
  const currentKey = current ? modelKey(current) : null;
  const currentModel = currentKey ? byKey.get(currentKey) : undefined;
  const recent = useMemo(
    () =>
      recentModels()
        .filter((key) => key !== currentKey)
        .flatMap((key) => byKey.get(key) ?? []),
    [byKey, currentKey],
  );
  const groups = useMemo(() => {
    const map = new Map<string, ModelInfo[]>();
    for (const model of models ?? [])
      map.set(model.provider, [...(map.get(model.provider) ?? []), model]);
    const name = (id: string) => providers?.find((p) => p.id === id)?.name ?? id;
    return [...map.entries()]
      .map(([id, items]) => ({ id, name: name(id), items }))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [models, providers]);

  const choose = async (model: ModelInfo) => {
    await request("set_model", { sessionId, provider: model.provider, modelId: model.id });
    rememberModel(modelKey(model));
    onDone();
  };
  const saveDefault = async () => {
    if (!current) return;
    await request("update_settings", {
      patch: { defaultProvider: current.provider, defaultModel: current.id },
    });
    toast(`Default model set to ${current.name ?? current.id}`);
  };
  const row = (model: ModelInfo, value: string) => (
    <Command.Item
      key={value}
      value={value}
      onSelect={() => void choose(model).catch(reportError)}
      className={modelRow}
    >
      <span className="min-w-0 flex-1 truncate">{model.name ?? model.id}</span>
      {modelKey(model) === currentKey ? (
        <Icon name="check" size={12} className="shrink-0 text-fg-muted" title="Current model" />
      ) : null}
    </Command.Item>
  );
  // Pinning saves scrolling; a short list already shows everything, so it skips the pins.
  const pinned = !query.trim() && (models?.length ?? 0) > PIN_AFTER;

  return (
    <Command
      label="Choose a model"
      loop
      className="flex max-h-[60dvh] w-[min(20rem,calc(100vw-2.5rem))] flex-col max-sm:w-full"
    >
      <Command.Input
        value={query}
        onValueChange={setQuery}
        autoFocus={autoFocus}
        placeholder="Search models"
        className="w-full border-b border-line bg-transparent px-2 pb-2 pt-1 text-[13px] outline-none placeholder:text-fg-faint pointer-coarse:text-base"
      />
      <Command.List className="mt-1 min-h-0 flex-1 overflow-y-auto">
        {models === null ? (
          <p className="p-2 text-[13px] text-fg-muted">Loading models…</p>
        ) : models.length === 0 ? (
          <p className="p-2 text-[13px] text-fg-muted">
            No models available. Add an API key or sign in under Settings → Providers.
          </p>
        ) : (
          <Command.Empty className="p-2 text-[13px] text-fg-muted">No models match.</Command.Empty>
        )}
        {pinned && currentModel ? (
          <Command.Group heading="Current" className={groupClass}>
            {row(currentModel, `current ${currentKey}`)}
          </Command.Group>
        ) : null}
        {pinned && recent.length ? (
          <Command.Group heading="Recent" className={groupClass}>
            {recent.map((model) => row(model, `recent ${modelKey(model)}`))}
          </Command.Group>
        ) : null}
        {groups.map((group) => (
          <Command.Group key={group.id} heading={group.name} className={groupClass}>
            {group.items.map((model) => row(model, `${modelKey(model)} ${model.name ?? ""}`))}
          </Command.Group>
        ))}
      </Command.List>
      <button
        type="button"
        onClick={() => saveDefault().catch(reportError)}
        disabled={!current}
        className="mt-1 flex w-full items-center border-t border-line px-2 pb-1 pt-2 text-left text-[12px] text-fg-muted hover:text-fg disabled:cursor-not-allowed pointer-coarse:min-h-11"
      >
        Save current model as default
      </button>
    </Command>
  );
}

/** The composer's model button: a popover with a mouse, the full-screen dialog on touch. */
export function ModelChip({ sessionId, model }: { sessionId: string; model: ModelInfo | null }) {
  const [open, setOpen] = useState(false);
  const touch = useMediaQuery(COARSE_POINTER);
  const name = model ? (model.name ?? model.id) : "No model";
  const label = (
    <>
      <span className="max-w-48 truncate max-sm:max-w-36">{shortModelName(name)}</span>
      <Icon name="chevronDown" size={11} />
    </>
  );
  const title = `${name} (${shortcutLabel("P")} to cycle)`;
  if (touch) {
    return (
      <button type="button" className={chipClass} title={title} onClick={() => ui.open("models")}>
        {label}
      </button>
    );
  }
  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger className={chipClass} title={title}>
        {label}
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          side="top"
          align="start"
          sideOffset={6}
          className="z-50 rounded-lg border border-line bg-float p-2 shadow-float"
        >
          <ModelList sessionId={sessionId} current={model} onDone={() => setOpen(false)} />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

export function ThinkingChip({ sessionId, level }: { sessionId: string; level: string }) {
  const [open, setOpen] = useState(false);
  const [levels, setLevels] = useState<string[]>([]);
  useEffect(() => {
    if (open)
      request<string[]>("get_available_thinking_levels", { sessionId })
        .then(setLevels)
        .catch(reportError);
  }, [open, sessionId]);
  const choose = async (value: string) => {
    await request("set_thinking_level", { sessionId, level: value as "off" });
    setOpen(false);
  };
  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger
        className={chipClass}
        title={`Thinking level (${shortcutLabel(".", true)} to cycle)`}
      >
        think: {level}
        <Icon name="chevronDown" size={11} />
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          side="top"
          align="start"
          sideOffset={6}
          className="z-50 w-44 rounded-lg border border-line bg-float p-1 shadow-float"
        >
          {levels.map((value) => (
            <button
              type="button"
              key={value}
              onClick={() => choose(value).catch(reportError)}
              className="flex w-full items-center rounded px-2 py-1 text-left text-[13px] hover:bg-hover pointer-coarse:min-h-11"
            >
              {value}
              {value === level ? <Icon name="check" size={12} className="ml-auto" /> : null}
            </button>
          ))}
          <button
            type="button"
            onClick={() =>
              request("update_settings", { patch: { defaultThinkingLevel: level } })
                .then(() => toast(`Default thinking level: ${level}`))
                .catch(reportError)
            }
            className="mt-1 w-full border-t border-line px-2 pt-1.5 text-left text-[12px] text-fg-muted hover:text-fg pointer-coarse:min-h-11 pointer-coarse:pt-0"
          >
            Save as default
          </button>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}
