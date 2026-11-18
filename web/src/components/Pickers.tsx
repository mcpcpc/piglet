import * as Popover from "@radix-ui/react-popover";
import { useEffect, useMemo, useState } from "react";
import { shortcutLabel } from "../lib/native";
import type { ModelInfo } from "../protocol/types";
import { reportError, request, toast } from "../state/store";
import { Icon } from "./Icon";
import { inputClass } from "./Modal";

const chipClass =
  "flex min-w-0 items-center gap-1 rounded-md px-2 py-1 text-[12px] text-fg-muted hover:bg-hover hover:text-fg pointer-coarse:min-h-11";

export function ModelList({
  sessionId,
  current,
  onDone,
}: {
  sessionId: string;
  current: ModelInfo | null;
  onDone: () => void;
}) {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [query, setQuery] = useState("");
  useEffect(() => {
    request<ModelInfo[]>("get_available_models", { sessionId }).then(setModels).catch(reportError);
  }, [sessionId]);
  const filtered = useMemo(() => {
    const q = query.toLowerCase();
    return models.filter((m) => `${m.provider}/${m.id} ${m.name ?? ""}`.toLowerCase().includes(q));
  }, [models, query]);
  const groups = useMemo(() => {
    const map = new Map<string, ModelInfo[]>();
    for (const model of filtered)
      map.set(model.provider, [...(map.get(model.provider) ?? []), model]);
    return [...map.entries()];
  }, [filtered]);

  const choose = async (model: ModelInfo) => {
    await request("set_model", { sessionId, provider: model.provider, modelId: model.id });
    onDone();
  };
  const saveDefault = async () => {
    if (!current) return;
    await request("update_settings", {
      patch: { defaultProvider: current.provider, defaultModel: current.id },
    });
    toast(`Default model: ${current.provider}/${current.id}`);
  };

  return (
    <div className="flex max-h-[60dvh] w-[min(20rem,calc(100vw-2.5rem))] flex-col">
      <input
        className={`${inputClass} mb-2`}
        placeholder="Search models"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        // biome-ignore lint/a11y/noAutofocus: the picker exists to type into
        autoFocus
      />
      <div className="min-h-0 flex-1 overflow-y-auto">
        {models.length === 0 ? (
          <p className="p-2 text-[13px] text-fg-muted">
            No models available. Add an API key or sign in under Settings → Providers.
          </p>
        ) : null}
        {groups.map(([provider, items]) => (
          <div key={provider} className="mb-2">
            <div className="px-2 py-1 text-[11px] uppercase tracking-wide text-fg-faint">
              {provider}
            </div>
            {items.map((model) => {
              const selected = current?.provider === model.provider && current.id === model.id;
              return (
                <button
                  type="button"
                  key={`${model.provider}/${model.id}`}
                  onClick={() => choose(model).catch(reportError)}
                  className={`flex w-full items-center gap-2 rounded px-2 py-1 text-left text-[13px] hover:bg-hover ${selected ? "font-medium" : ""}`}
                >
                  <span className="min-w-0 flex-1 truncate">{model.name ?? model.id}</span>
                  {model.reasoning ? (
                    <span className="text-[10px] text-fg-faint">reasoning</span>
                  ) : null}
                  {selected ? <Icon name="check" size={12} /> : null}
                </button>
              );
            })}
          </div>
        ))}
      </div>
      <button
        type="button"
        onClick={() => saveDefault().catch(reportError)}
        className="mt-2 border-t border-line pt-2 text-left text-[12px] text-fg-muted hover:text-fg"
        disabled={!current}
      >
        Save current model as default
      </button>
    </div>
  );
}

export function ModelChip({ sessionId, model }: { sessionId: string; model: ModelInfo | null }) {
  const [open, setOpen] = useState(false);
  return (
    <Popover.Root open={open} onOpenChange={setOpen}>
      <Popover.Trigger className={chipClass} title={`Model (${shortcutLabel("P")} to cycle)`}>
        <span className="max-w-48 truncate max-sm:max-w-28">
          {model ? (model.name ?? model.id) : "No model"}
        </span>
        <Icon name="chevronDown" size={11} />
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
