// Settings: a GUI over settings.json (global), models.json, auth.json and the project file.

import { type ReactNode, useCallback, useEffect, useState } from "react";
import type { ModelInfo } from "../protocol/types";
import {
  activeView,
  refreshSettings,
  reportError,
  request,
  toast,
  ui,
  useApp,
} from "../state/store";
import { Button, inputClass, Modal } from "./Modal";

type Settings = Record<string, unknown>;

interface SettingsData {
  global: Settings;
  project: Settings | null;
  defaults: Settings;
  agentDir: string;
  globalPath: string;
  projectPath: string | null;
}

interface ProviderStatus {
  provider: string;
  configured: boolean;
  source: string | null;
  type: string | null;
  envVars: string[];
}

const TABS = [
  ["general", "General"],
  ["models", "Models"],
  ["providers", "Providers"],
  ["tools", "Tools"],
  ["compaction", "Compaction & retry"],
  ["resources", "Resources"],
  ["project", "Project"],
] as const;

const TOOLS = ["read", "bash", "edit", "write", "grep", "find", "ls"];
const LEVELS = ["off", "minimal", "low", "medium", "high", "xhigh", "max"];

function getPath(doc: Settings, path: string): unknown {
  return path
    .split(".")
    .reduce<unknown>(
      (node, key) => (node && typeof node === "object" ? (node as Settings)[key] : undefined),
      doc,
    );
}

function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    // biome-ignore lint/a11y/noLabelWithoutControl: the control is passed as children
    <label className="grid grid-cols-[180px_1fr] items-center gap-3 py-1.5 max-sm:grid-cols-1">
      <span className="text-[13px]">
        {label}
        {hint ? <span className="block text-[11px] text-fg-faint">{hint}</span> : null}
      </span>
      <span>{children}</span>
    </label>
  );
}

function useSettings() {
  const [data, setData] = useState<SettingsData | null>(null);
  const load = useCallback(() => {
    const cwd = activeView()?.state.cwd ?? null;
    request<SettingsData>("get_settings", { cwd }).then(setData).catch(reportError);
  }, []);
  useEffect(load, [load]);
  const patch = async (value: Settings) => {
    await request("update_settings", { patch: value });
    load();
    await refreshSettings();
  };
  return { data, patch, load };
}

function value(data: SettingsData, path: string): unknown {
  return getPath(data.global, path) ?? getPath(data.defaults, path);
}

function Toggle({
  data,
  path,
  patch,
}: {
  data: SettingsData;
  path: string;
  patch: (v: Settings) => Promise<void>;
}) {
  const checked = Boolean(value(data, path));
  const keys = path.split(".");
  const nested = keys.reduceRight<unknown>((acc, key) => ({ [key]: acc }), !checked) as Settings;
  return (
    <input
      type="checkbox"
      checked={checked}
      onChange={() => void patch(nested).catch(reportError)}
    />
  );
}

function Select({
  data,
  path,
  options,
  patch,
}: {
  data: SettingsData;
  path: string;
  options: string[];
  patch: (v: Settings) => Promise<void>;
}) {
  const current = String(value(data, path) ?? "");
  const keys = path.split(".");
  return (
    <select
      className={inputClass}
      value={current}
      onChange={(e) =>
        void patch(
          keys.reduceRight<unknown>((acc, key) => ({ [key]: acc }), e.target.value) as Settings,
        ).catch(reportError)
      }
    >
      {options.map((option) => (
        <option key={option} value={option}>
          {option}
        </option>
      ))}
    </select>
  );
}

function NumberInput({
  data,
  path,
  patch,
}: {
  data: SettingsData;
  path: string;
  patch: (v: Settings) => Promise<void>;
}) {
  const [text, setText] = useState(String(value(data, path) ?? ""));
  const keys = path.split(".");
  const save = () => {
    const parsed = Number(text);
    if (!Number.isFinite(parsed)) return;
    void patch(keys.reduceRight<unknown>((acc, key) => ({ [key]: acc }), parsed) as Settings).catch(
      reportError,
    );
  };
  return (
    <input
      className={inputClass}
      inputMode="numeric"
      value={text}
      onChange={(e) => setText(e.target.value)}
      onBlur={save}
    />
  );
}

function TextInput({
  data,
  path,
  patch,
  placeholder,
}: {
  data: SettingsData;
  path: string;
  patch: (v: Settings) => Promise<void>;
  placeholder?: string;
}) {
  const [text, setText] = useState(String(value(data, path) ?? ""));
  return (
    <input
      className={inputClass}
      value={text}
      placeholder={placeholder}
      onChange={(e) => setText(e.target.value)}
      onBlur={() => void patch({ [path]: text || null }).catch(reportError)}
    />
  );
}

function General({ data, patch }: { data: SettingsData; patch: (v: Settings) => Promise<void> }) {
  return (
    <div>
      <Field label="Appearance">
        <Select data={data} path="theme" options={["system", "light", "dark"]} patch={patch} />
      </Field>
      <Field label="Default thinking level">
        <Select data={data} path="defaultThinkingLevel" options={LEVELS} patch={patch} />
      </Field>
      <Field label="Steering delivery" hint="Messages sent while the agent works">
        <Select data={data} path="steeringMode" options={["one-at-a-time", "all"]} patch={patch} />
      </Field>
      <Field label="Follow-up delivery">
        <Select data={data} path="followUpMode" options={["one-at-a-time", "all"]} patch={patch} />
      </Field>
      <Field label="Hide thinking blocks">
        <Toggle data={data} path="hideThinkingBlock" patch={patch} />
      </Field>
      <Field label="Project trust default" hint="For projects with a .piglet folder">
        <Select
          data={data}
          path="defaultProjectTrust"
          options={["ask", "always", "never"]}
          patch={patch}
        />
      </Field>
      <Field label="Shell path" hint="Used by the bash tool">
        <TextInput data={data} path="shellPath" patch={patch} placeholder="/bin/bash" />
      </Field>
      <Field label="Session directory" hint="Leave empty for the default">
        <TextInput
          data={data}
          path="sessionDir"
          patch={patch}
          placeholder={`${data.agentDir}/sessions`}
        />
      </Field>
      <Field label="Auto-resize images">
        <Toggle data={data} path="images.autoResize" patch={patch} />
      </Field>
      <Field label="Block images">
        <Toggle data={data} path="images.blockImages" patch={patch} />
      </Field>
      <p className="mt-3 text-[11px] text-fg-faint">Saved to {data.globalPath}</p>
    </div>
  );
}

function Models({ data, patch }: { data: SettingsData; patch: (v: Settings) => Promise<void> }) {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [scoped, setScoped] = useState(((data.global.enabledModels as string[]) ?? []).join("\n"));
  const [config, setConfig] = useState<string | null>(null);
  useEffect(() => {
    request<ModelInfo[]>("list_models", { cwd: activeView()?.state.cwd ?? null })
      .then(setModels)
      .catch(reportError);
    request<{ text: string }>("get_models_config", {})
      .then((r) => setConfig(r.text))
      .catch(reportError);
  }, []);
  const current = `${String(data.global.defaultProvider ?? "")}/${String(data.global.defaultModel ?? "")}`;
  const budgets = (value(data, "thinkingBudgets") ?? {}) as Record<string, number>;
  return (
    <div className="space-y-4">
      <Field label="Default model">
        <select
          className={inputClass}
          value={current}
          onChange={(e) => {
            const [provider, ...rest] = e.target.value.split("/");
            void patch({ defaultProvider: provider, defaultModel: rest.join("/") }).catch(
              reportError,
            );
          }}
        >
          <option value="/">(automatic)</option>
          {models
            .filter((m) => m.supported)
            .map((m) => (
              <option key={`${m.provider}/${m.id}`} value={`${m.provider}/${m.id}`}>
                {m.provider}/{m.id}
              </option>
            ))}
        </select>
      </Field>
      <Field
        label="Scoped models"
        hint="One pattern per line (provider/id, globs, :level). Used by ⌘P cycling."
      >
        <textarea
          className={`${inputClass} h-24 font-mono`}
          value={scoped}
          onChange={(e) => setScoped(e.target.value)}
          onBlur={() => {
            const list = scoped
              .split("\n")
              .map((s) => s.trim())
              .filter(Boolean);
            void patch({ enabledModels: list.length ? list : null }).catch(reportError);
          }}
        />
      </Field>
      <Field label="Thinking budgets" hint="Tokens for budget-based models">
        <div className="grid grid-cols-4 gap-2">
          {["minimal", "low", "medium", "high"].map((level) => (
            <label key={level} className="text-[11px] text-fg-faint">
              {level}
              <input
                className={inputClass}
                defaultValue={budgets[level]}
                onBlur={(e) =>
                  void patch({ thinkingBudgets: { [level]: Number(e.target.value) } }).catch(
                    reportError,
                  )
                }
              />
            </label>
          ))}
        </div>
      </Field>
      <div>
        <div className="mb-1 flex items-center text-[13px]">
          models.json{" "}
          <span className="ml-2 text-[11px] text-fg-faint">
            custom providers, models and overrides
          </span>
        </div>
        {config !== null ? (
          <>
            <textarea
              className={`${inputClass} h-48 font-mono text-[12px]`}
              value={config}
              onChange={(e) => setConfig(e.target.value)}
              spellCheck={false}
            />
            <Button
              className="mt-2"
              onClick={() =>
                request("save_models_config", { text: config })
                  .then(() => toast("models.json saved"))
                  .catch(reportError)
              }
            >
              Save models.json
            </Button>
          </>
        ) : null}
      </div>
    </div>
  );
}

function Providers() {
  const [providers, setProviders] = useState<ProviderStatus[]>([]);
  const [editing, setEditing] = useState<string | null>(null);
  const [key, setKey] = useState("");
  const load = useCallback(() => {
    request<ProviderStatus[]>("auth_status", {}).then(setProviders).catch(reportError);
  }, []);
  useEffect(load, [load]);
  const save = async (provider: string) => {
    await request("set_api_key", { provider, key });
    setEditing(null);
    setKey("");
    load();
  };
  return (
    <div className="space-y-1">
      <p className="mb-2 text-[12px] text-fg-muted">
        Keys are stored in auth.json (mode 0600). Values may be literal keys, <code>$ENV_VAR</code>{" "}
        references or <code>!command</code> to read from a password manager.{" "}
        <button type="button" className="text-accent underline" onClick={() => ui.open("login")}>
          Sign in with a subscription…
        </button>
      </p>
      {providers.map((p) => (
        <div key={p.provider} className="rounded-md border border-line px-3 py-2">
          <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 text-[13px]">
            <div className="min-w-0">
              <div className="font-medium [overflow-wrap:anywhere]">{p.provider}</div>
              {p.configured ? (
                <div className="text-[11px] text-add-fg">
                  {p.type === "oauth" ? "signed in" : "configured"} · {p.source}
                </div>
              ) : p.envVars.length ? (
                <div className="text-[11px] text-fg-faint [overflow-wrap:anywhere]">
                  or set {p.envVars.join(" / ")}
                </div>
              ) : null}
            </div>
            <span className="flex gap-1">
              <Button
                variant="ghost"
                onClick={() => setEditing(editing === p.provider ? null : p.provider)}
              >
                {p.source === "auth.json" ? "Replace key" : "Add key"}
              </Button>
              {p.source === "auth.json" ? (
                <Button
                  variant="danger"
                  onClick={() =>
                    request("logout", { provider: p.provider }).then(load).catch(reportError)
                  }
                >
                  Remove
                </Button>
              ) : null}
            </span>
          </div>
          {editing === p.provider ? (
            <form
              className="mt-2 flex gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                void save(p.provider).catch(reportError);
              }}
            >
              <input
                className={inputClass}
                type="password"
                autoComplete="off"
                placeholder="sk-…, $ENV or !command"
                value={key}
                onChange={(e) => setKey(e.target.value)}
              />
              <Button type="submit" variant="primary" disabled={!key}>
                Save
              </Button>
            </form>
          ) : null}
        </div>
      ))}
    </div>
  );
}

function Tools({ data, patch }: { data: SettingsData; patch: (v: Settings) => Promise<void> }) {
  const configured = data.global.defaultTools as string[] | undefined;
  const enabled = new Set(
    configured?.filter((t) => !/^[+-]/.test(t)) ?? ["read", "bash", "edit", "write"],
  );
  const toggle = (tool: string) => {
    const next = new Set(enabled);
    if (next.has(tool)) next.delete(tool);
    else next.add(tool);
    void patch({ defaultTools: TOOLS.filter((t) => next.has(t)) }).catch(reportError);
  };
  return (
    <div className="space-y-1">
      <p className="mb-2 text-[12px] text-fg-muted">
        Built-in tools available to the model. Project settings may add or remove tools with +name /
        -name.
      </p>
      {TOOLS.map((tool) => (
        <label key={tool} className="flex items-center gap-2 py-1 font-mono text-[13px]">
          <input type="checkbox" checked={enabled.has(tool)} onChange={() => toggle(tool)} />
          {tool}
        </label>
      ))}
    </div>
  );
}

function Compaction({
  data,
  patch,
}: {
  data: SettingsData;
  patch: (v: Settings) => Promise<void>;
}) {
  return (
    <div>
      <Field label="Auto-compaction">
        <Toggle data={data} path="compaction.enabled" patch={patch} />
      </Field>
      <Field label="Reserve tokens" hint="Compact when context exceeds window − reserve">
        <NumberInput data={data} path="compaction.reserveTokens" patch={patch} />
      </Field>
      <Field label="Keep recent tokens">
        <NumberInput data={data} path="compaction.keepRecentTokens" patch={patch} />
      </Field>
      <Field label="Skip branch summary prompt" hint="When moving in the tree">
        <Toggle data={data} path="branchSummary.skipPrompt" patch={patch} />
      </Field>
      <Field label="Auto-retry">
        <Toggle data={data} path="retry.enabled" patch={patch} />
      </Field>
      <Field label="Max retries">
        <NumberInput data={data} path="retry.maxRetries" patch={patch} />
      </Field>
      <Field label="Base delay (ms)">
        <NumberInput data={data} path="retry.baseDelayMs" patch={patch} />
      </Field>
      <Field label="Max delay (ms)">
        <NumberInput data={data} path="retry.maxAgentDelayMs" patch={patch} />
      </Field>
    </div>
  );
}

function Resources({ data }: { data: SettingsData }) {
  const commands = useApp((s) => (s.activeId ? s.commands[s.activeId] : undefined)) ?? [];
  const groups: [string, string][] = [
    ["skill", "Skills"],
    ["prompt", "Prompt templates"],
    ["extension", "Extension commands"],
  ];
  return (
    <div className="space-y-4">
      <p className="text-[12px] text-fg-muted">
        Skills live in {data.agentDir}/skills and .piglet/skills; prompt templates in{" "}
        {data.agentDir}/prompts and .piglet/prompts. Use /reload after editing them.
      </p>
      {groups.map(([source, title]) => {
        const items = commands.filter((c) => c.source === source);
        return (
          <section key={source}>
            <h3 className="mb-1 text-[12px] font-medium uppercase tracking-wide text-fg-muted">
              {title}
            </h3>
            {items.length ? (
              items.map((c) => (
                <div key={c.name} className="flex gap-3 py-0.5 text-[13px]">
                  <span className="font-mono">/{c.name}</span>
                  <span className="truncate text-fg-faint">{c.description}</span>
                </div>
              ))
            ) : (
              <p className="text-[12px] text-fg-faint">None loaded for this session.</p>
            )}
          </section>
        );
      })}
      <PackagesSection />
    </div>
  );
}

function PackagesSection() {
  const [packages, setPackages] = useState<{ source: string; installed: boolean }[] | null>(null);
  const [source, setSource] = useState("");
  const load = useCallback(() => {
    request<{ source: string; installed: boolean }[]>("list_packages", {})
      .then(setPackages)
      .catch(() => setPackages(null));
  }, []);
  useEffect(load, [load]);
  if (packages === null) return null;
  return (
    <section>
      <h3 className="mb-1 text-[12px] font-medium uppercase tracking-wide text-fg-muted">
        Packages
      </h3>
      {packages.map((p) => (
        <div key={p.source} className="flex items-center gap-2 py-0.5 text-[13px]">
          <span className="min-w-0 flex-1 truncate font-mono">{p.source}</span>
          <Button
            variant="danger"
            onClick={() =>
              request("remove_package", { source: p.source }).then(load).catch(reportError)
            }
          >
            Remove
          </Button>
        </div>
      ))}
      <form
        className="mt-2 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          request("install_package", { source })
            .then(() => {
              setSource("");
              load();
              toast("Package installed. Use /reload to load it.");
            })
            .catch(reportError);
        }}
      >
        <input
          className={inputClass}
          placeholder="git URL or local path"
          value={source}
          onChange={(e) => setSource(e.target.value)}
        />
        <Button type="submit" disabled={!source}>
          Install
        </Button>
      </form>
    </section>
  );
}

function Project({ data, reload }: { data: SettingsData; reload: () => void }) {
  const cwd = activeView()?.state.cwd;
  const [text, setText] = useState(JSON.stringify(data.project ?? {}, null, 2));
  if (!cwd)
    return (
      <p className="text-[13px] text-fg-muted">Open a session to edit its project settings.</p>
    );
  const save = async () => {
    const parsed = JSON.parse(text || "{}");
    await request("set_project_settings", { cwd, settings: parsed });
    toast("Project settings saved");
    reload();
  };
  return (
    <div>
      <p className="mb-2 text-[12px] text-fg-muted">
        {data.projectPath} (merged over global settings; loaded only when the project is trusted)
      </p>
      <textarea
        className={`${inputClass} h-64 font-mono text-[12px]`}
        value={text}
        onChange={(e) => setText(e.target.value)}
        spellCheck={false}
      />
      <Button className="mt-2" onClick={() => void save().catch(reportError)}>
        Save project settings
      </Button>
    </div>
  );
}

export function SettingsDialog() {
  const open = useApp((s) => s.dialog === "settings");
  const tab = useApp((s) => s.settingsTab);
  const { data, patch, load } = useSettings();
  useEffect(() => {
    if (open) load();
  }, [open, load]);
  return (
    <Modal open={open} onClose={ui.close} title="Settings" width="max-w-3xl">
      <div className="flex gap-4 max-sm:flex-col">
        <nav className="flex w-40 shrink-0 flex-col gap-0.5 max-sm:w-full max-sm:flex-row max-sm:flex-wrap max-sm:gap-1">
          {TABS.map(([id, label]) => (
            <button
              type="button"
              key={id}
              onClick={() => ui.open("settings", id)}
              className={`whitespace-nowrap rounded-md px-2 py-1 text-left text-[13px] pointer-coarse:py-2 ${tab === id ? "bg-muted text-fg" : "text-fg-muted hover:text-fg"}`}
            >
              {label}
            </button>
          ))}
        </nav>
        <div className="min-w-0 flex-1">
          {!data ? null : tab === "general" ? (
            <General data={data} patch={patch} />
          ) : tab === "models" ? (
            <Models data={data} patch={patch} />
          ) : tab === "providers" ? (
            <Providers />
          ) : tab === "tools" ? (
            <Tools data={data} patch={patch} />
          ) : tab === "compaction" ? (
            <Compaction data={data} patch={patch} />
          ) : tab === "resources" ? (
            <Resources data={data} />
          ) : (
            <Project data={data} reload={load} />
          )}
        </div>
      </div>
    </Modal>
  );
}
