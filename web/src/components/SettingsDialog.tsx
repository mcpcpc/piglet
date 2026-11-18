// Settings: a GUI over settings.json (global), models.json, auth.json and the project file.
// Every tab is built from SettingsKit: quiet sections of rows, one control per kind of decision.

import { type KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import { useMediaQuery } from "../lib/media";
import { shortcutLabel } from "../lib/native";
import { reasonText } from "../lib/providers";
import type { ModelInfo } from "../protocol/types";
import {
  activeView,
  refreshProviders,
  refreshSettings,
  reportError,
  request,
  ui,
  useApp,
} from "../state/store";
import {
  type EscapeRef,
  ModelsWarning,
  ProviderList,
  ProviderPage,
  ProvidersLoading,
  RefreshFailed,
  TOUCH_TARGETS,
} from "./Credentials";
import { Button, inputClass, Modal } from "./Modal";
import {
  type Choice,
  ChoiceSetting,
  FileEditor,
  InlineConfirm,
  NumberSetting,
  type Patch,
  SaveReporterContext,
  type SaveStatus,
  Section,
  SelectSetting,
  type Settings,
  StatusText,
  SwitchSetting,
  TextSetting,
  useDialogSaveStatus,
} from "./SettingsKit";

interface SettingsData {
  global: Settings;
  project: Settings | null;
  defaults: Settings;
  agentDir: string;
  globalPath: string;
  projectPath: string | null;
}

interface TabProps {
  data: SettingsData;
  patch: Patch;
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

/** Below Tailwind's sm: the tab column becomes a row above the panel. */
const NARROW = "(max-width: 639.98px)";

const TOOLS: [string, string][] = [
  ["read", "Read file contents"],
  ["bash", "Run shell commands"],
  ["edit", "Make exact text replacements in files"],
  ["write", "Create or overwrite files"],
  ["grep", "Search file contents for patterns"],
  ["find", "Find files by glob pattern"],
  ["ls", "List directory contents"],
];
const DEFAULT_TOOLS = ["read", "bash", "edit", "write"];

const LEVELS: [string, string][] = [
  ["off", "Off"],
  ["minimal", "Minimal"],
  ["low", "Low"],
  ["medium", "Medium"],
  ["high", "High"],
  ["xhigh", "Extra high"],
  ["max", "Max"],
];
const BUDGETS: [string, string][] = [
  ["minimal", "Minimal"],
  ["low", "Low"],
  ["medium", "Medium"],
  ["high", "High"],
];

const THEMES: Choice<string>[] = [
  { value: "system", label: "System" },
  { value: "light", label: "Light" },
  { value: "dark", label: "Dark" },
];
const DELIVERY: Choice<string>[] = [
  { value: "one-at-a-time", label: "One at a time" },
  { value: "all", label: "All at once" },
];
const TRUST: Choice<string>[] = [
  { value: "ask", label: "Ask" },
  { value: "always", label: "Always" },
  { value: "never", label: "Never" },
];

function getPath(doc: Settings, path: string): unknown {
  return path
    .split(".")
    .reduce<unknown>(
      (node, key) => (node && typeof node === "object" ? (node as Settings)[key] : undefined),
      doc,
    );
}

/** The global value, else the default. */
function value(data: SettingsData, path: string): unknown {
  return getPath(data.global, path) ?? getPath(data.defaults, path);
}

function text(data: SettingsData, path: string): string {
  const v = value(data, path);
  return v === undefined || v === null ? "" : String(v);
}

function useSettings() {
  const [data, setData] = useState<SettingsData | null>(null);
  const [error, setError] = useState<string | null>(null);
  // No load on mount: SettingsDialog is always mounted, and on page load the socket is still
  // connecting. It loads each time the dialog opens instead.
  const load = useCallback(async () => {
    const cwd = activeView()?.state.cwd ?? null;
    try {
      setData(await request<SettingsData>("get_settings", { cwd }));
      setError(null);
    } catch (err) {
      setError(reasonText(err));
    }
  }, []);
  // update_settings answers with the new global document, so the row shows its saved state as
  // soon as the save returns.
  const patch = useCallback(async (value: Settings) => {
    const global = await request<Settings | null>("update_settings", { patch: value });
    setData((current) =>
      current && global && typeof global === "object" ? { ...current, global } : current,
    );
    void refreshSettings().catch(reportError);
  }, []);
  return { data, error, patch, load };
}

function General({ data, patch }: TabProps) {
  return (
    <>
      <Section title="Appearance">
        <ChoiceSetting
          label="Theme"
          value={text(data, "theme")}
          choices={THEMES}
          saveAs="theme"
          patch={patch}
        />
        <SwitchSetting
          label="Hide thinking blocks"
          value={Boolean(value(data, "hideThinkingBlock"))}
          saveAs="hideThinkingBlock"
          patch={patch}
        />
      </Section>
      <Section title="Messages">
        <SelectSetting
          label="Default thinking level"
          value={text(data, "defaultThinkingLevel")}
          saveAs="defaultThinkingLevel"
          patch={patch}
        >
          {LEVELS.map(([id, label]) => (
            <option key={id} value={id}>
              {label}
            </option>
          ))}
        </SelectSetting>
        <ChoiceSetting
          label="Steering delivery"
          hint="Messages sent while the agent works"
          value={text(data, "steeringMode")}
          choices={DELIVERY}
          saveAs="steeringMode"
          patch={patch}
        />
        <ChoiceSetting
          label="Follow-up delivery"
          value={text(data, "followUpMode")}
          choices={DELIVERY}
          saveAs="followUpMode"
          patch={patch}
        />
      </Section>
      <Section title="Projects">
        <ChoiceSetting
          label="Project trust default"
          hint="For projects with a .piglet folder"
          value={text(data, "defaultProjectTrust")}
          choices={TRUST}
          saveAs="defaultProjectTrust"
          patch={patch}
        />
        <TextSetting
          label="Session directory"
          hint={`Leave empty to use ${data.agentDir}/sessions`}
          placeholder="Default"
          value={text(data, "sessionDir")}
          saveAs="sessionDir"
          patch={patch}
        />
      </Section>
      <Section title="Shell">
        <TextSetting
          label="Shell path"
          hint="Used by the bash tool"
          placeholder="/bin/bash"
          value={text(data, "shellPath")}
          saveAs="shellPath"
          patch={patch}
        />
      </Section>
      <Section title="Images">
        <SwitchSetting
          label="Auto-resize images"
          value={Boolean(value(data, "images.autoResize"))}
          saveAs="images.autoResize"
          patch={patch}
        />
        <SwitchSetting
          label="Block images"
          value={Boolean(value(data, "images.blockImages"))}
          saveAs="images.blockImages"
          patch={patch}
        />
      </Section>
      <SavedTo path={data.globalPath} />
    </>
  );
}

function SavedTo({ path }: { path: string }) {
  return <p className="mt-6 text-[11px] text-fg-muted [overflow-wrap:anywhere]">Saved to {path}</p>;
}

function modelKey(m: ModelInfo): string {
  return `${m.provider}/${m.id}`;
}

function Models({ data, patch }: TabProps) {
  const [models, setModels] = useState<ModelInfo[]>([]);
  const [config, setConfig] = useState<string | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  useEffect(() => {
    request<ModelInfo[]>("list_models", { cwd: activeView()?.state.cwd ?? null })
      .then((list) => setModels(Array.isArray(list) ? list : []))
      .catch(reportError);
    request<{ text: string }>("get_models_config", {})
      .then((r) => setConfig(r?.text ?? ""))
      .catch((err) => setConfigError(reasonText(err)));
  }, []);
  const provider = String(data.global.defaultProvider ?? "");
  const model = String(data.global.defaultModel ?? "");
  const current = provider && model ? `${provider}/${model}` : "";
  const supported = models.filter((m) => m.supported);
  const groups = new Map<string, ModelInfo[]>();
  for (const m of supported) groups.set(m.provider, [...(groups.get(m.provider) ?? []), m]);
  const scoped = ((data.global.enabledModels as string[] | undefined) ?? []).join("\n");
  return (
    <>
      <Section>
        <SelectSetting
          label="Default model"
          value={current}
          saveAs={(key) => {
            if (!key) return { defaultProvider: null, defaultModel: null };
            const [p, ...rest] = key.split("/");
            return { defaultProvider: p, defaultModel: rest.join("/") };
          }}
          patch={patch}
        >
          <option value="">Automatic</option>
          {current && !supported.some((m) => modelKey(m) === current) ? (
            <option value={current}>{current}</option>
          ) : null}
          {[...groups].map(([name, list]) => (
            <optgroup key={name} label={name}>
              {list.map((m) => (
                <option key={modelKey(m)} value={modelKey(m)}>
                  {m.name ?? m.id}
                </option>
              ))}
            </optgroup>
          ))}
        </SelectSetting>
        <TextSetting
          label="Scoped models"
          hint={`One pattern per line (provider/id, globs, :level). Used by ${shortcutLabel("P")} cycling.`}
          value={scoped}
          multiline
          saveAs={(lines) => {
            const list = (lines ?? "")
              .split("\n")
              .map((s) => s.trim())
              .filter(Boolean);
            return { enabledModels: list.length ? list : null };
          }}
          patch={patch}
        />
      </Section>
      <Section title="Thinking budgets" intro="Tokens for budget-based models">
        {BUDGETS.map(([level, label]) => (
          <NumberSetting
            key={level}
            label={label}
            unit="tokens"
            value={text(data, `thinkingBudgets.${level}`)}
            saveAs={`thinkingBudgets.${level}`}
            patch={patch}
          />
        ))}
      </Section>
      <Section title="models.json" intro="Custom providers, models and overrides">
        {configError ? (
          <p role="alert" className="py-2 text-[13px]">
            Couldn't load models.json. {configError}
          </p>
        ) : config !== null ? (
          <FileEditor
            label="models.json"
            initial={config}
            saveLabel="Save models.json"
            onSave={async (next) => {
              await request("save_models_config", { text: next });
              setConfig(next);
            }}
          />
        ) : (
          <p className="py-2 text-[13px] text-fg-muted">Loading…</p>
        )}
      </Section>
    </>
  );
}

function Providers({ escapeRef }: { escapeRef: EscapeRef }) {
  const status = useApp((s) => s.providers);
  const error = useApp((s) => s.providersError);
  const [selected, setSelected] = useState("");
  const [page, setPage] = useState<string | null>(null);
  const pageEscape: EscapeRef = useRef(null);
  const root = useRef<HTMLDivElement>(null);
  const scrollTop = useRef(0);
  const provider = page ? status?.providers.find((p) => p.id === page) : undefined;

  useEffect(() => {
    void refreshProviders();
  }, []);
  // A provider that disappears (its last credential removed) returns to the list.
  useEffect(() => {
    if (page && status && !provider) setPage(null);
  }, [page, status, provider]);

  const scroller = () => root.current?.closest<HTMLElement>("[data-settings-scroll]");
  const open = (id: string) => {
    scrollTop.current = scroller()?.scrollTop ?? 0;
    setSelected(id);
    setPage(id);
    requestAnimationFrame(() => {
      const body = scroller();
      if (body) body.scrollTop = 0;
    });
  };
  const back = () => {
    setPage(null);
    requestAnimationFrame(() => {
      const body = scroller();
      if (body) body.scrollTop = scrollTop.current;
      const list = root.current;
      (
        list?.querySelector<HTMLElement>("[cmdk-input]") ??
        list?.querySelector<HTMLElement>("[cmdk-root]")
      )?.focus();
    });
  };

  useEffect(() => {
    escapeRef.current = () => {
      if (!page) return false;
      if (!pageEscape.current?.()) back();
      return true;
    };
    return () => {
      escapeRef.current = null;
    };
  });

  if (!status) return <ProvidersLoading error={error} />;
  return (
    <div ref={root} className={TOUCH_TARGETS}>
      {provider ? (
        <ProviderPage
          key={provider.id}
          p={provider}
          context="settings"
          backLabel="Back to providers"
          onBack={back}
          escapeRef={pageEscape}
        />
      ) : null}
      <div hidden={Boolean(provider)}>
        <p className="mb-3 text-[13px] text-fg-muted">
          Sign in or add API keys for model providers. Credentials saved here take priority over
          models.json and environment variables.
        </p>
        <RefreshFailed error={error} />
        <ModelsWarning warnings={status.warnings} />
        <ProviderList
          providers={status.providers}
          selected={selected}
          onSelected={setSelected}
          onOpen={(p) => open(p.id)}
          grouped
        />
      </div>
    </div>
  );
}

function Tools({ data, patch }: TabProps) {
  const configured = data.global.defaultTools as string[] | undefined;
  const enabled = new Set(configured?.filter((t) => !/^[+-]/.test(t)) ?? DEFAULT_TOOLS);
  return (
    <Section
      intro={
        <>
          Built-in tools available to the model. Project settings may add or remove tools with{" "}
          <code className="whitespace-nowrap">+name</code> /{" "}
          <code className="whitespace-nowrap">-name</code>.
        </>
      }
    >
      {TOOLS.map(([tool, description]) => (
        <SwitchSetting
          key={tool}
          label={<span className="font-mono">{tool}</span>}
          hint={description}
          value={enabled.has(tool)}
          saveAs={(on) => ({
            defaultTools: TOOLS.map(([t]) => t).filter((t) => (t === tool ? on : enabled.has(t))),
          })}
          patch={patch}
        />
      ))}
    </Section>
  );
}

function Compaction({ data, patch }: TabProps) {
  const compaction = Boolean(value(data, "compaction.enabled"));
  const retry = Boolean(value(data, "retry.enabled"));
  return (
    <>
      <Section title="Compaction">
        <SwitchSetting
          label="Auto-compaction"
          value={compaction}
          saveAs="compaction.enabled"
          patch={patch}
        />
        <NumberSetting
          label="Reserve tokens"
          hint="Compact when context exceeds window − reserve"
          unit="tokens"
          value={text(data, "compaction.reserveTokens")}
          saveAs="compaction.reserveTokens"
          patch={patch}
          disabled={!compaction}
        />
        <NumberSetting
          label="Keep recent tokens"
          unit="tokens"
          value={text(data, "compaction.keepRecentTokens")}
          saveAs="compaction.keepRecentTokens"
          patch={patch}
          disabled={!compaction}
        />
      </Section>
      <Section title="Session tree">
        <SwitchSetting
          label="Skip branch summary prompt"
          hint="When moving in the tree"
          value={Boolean(value(data, "branchSummary.skipPrompt"))}
          saveAs="branchSummary.skipPrompt"
          patch={patch}
        />
      </Section>
      <Section title="Retry">
        <SwitchSetting label="Auto-retry" value={retry} saveAs="retry.enabled" patch={patch} />
        <NumberSetting
          label="Max retries"
          unit="retries"
          value={text(data, "retry.maxRetries")}
          saveAs="retry.maxRetries"
          patch={patch}
          disabled={!retry}
        />
        <NumberSetting
          label="Base delay"
          unit="ms"
          value={text(data, "retry.baseDelayMs")}
          saveAs="retry.baseDelayMs"
          patch={patch}
          disabled={!retry}
        />
        <NumberSetting
          label="Max delay"
          unit="ms"
          value={text(data, "retry.maxAgentDelayMs")}
          saveAs="retry.maxAgentDelayMs"
          patch={patch}
          disabled={!retry}
        />
      </Section>
    </>
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
    <>
      <p className="mb-5 text-[12px] text-fg-muted [overflow-wrap:anywhere]">
        Skills live in {data.agentDir}/skills and .piglet/skills; prompt templates in{" "}
        {data.agentDir}/prompts and .piglet/prompts. Use /reload after editing them.
      </p>
      {groups.map(([source, title]) => {
        const items = commands.filter((c) => c.source === source);
        return (
          <Section key={source} title={title}>
            {items.length ? (
              <ul>
                {items.map((c) => (
                  <li key={c.name} className="flex flex-col py-1">
                    <span className="font-mono text-[13px] [overflow-wrap:anywhere]">
                      /{c.name}
                    </span>
                    {c.description ? (
                      <span className="text-[13px] text-fg-muted [overflow-wrap:anywhere]">
                        {c.description}
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="py-1 text-[12px] text-fg-muted">None loaded for this session.</p>
            )}
          </Section>
        );
      })}
      <PackagesSection />
    </>
  );
}

interface Package {
  source: string;
  installed: boolean;
}

function PackagesSection() {
  const [packages, setPackages] = useState<Package[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState<string | null>(null);
  const load = useCallback(async () => {
    try {
      const list = await request<Package[]>("list_packages", {});
      setPackages(Array.isArray(list) ? list : []);
      setError(null);
    } catch (err) {
      setError(reasonText(err));
    }
  }, []);
  useEffect(() => {
    void load();
  }, [load]);
  return (
    <Section title="Packages">
      {error && packages === null ? (
        <div role="alert" className="flex flex-wrap items-center gap-2 py-1.5 text-[13px]">
          <span className="min-w-0 flex-1">Couldn't load packages. {error}</span>
          <Button onClick={() => void load()}>Retry</Button>
        </div>
      ) : null}
      {packages?.length === 0 ? (
        <p className="py-1 text-[12px] text-fg-muted">No packages installed.</p>
      ) : null}
      {packages?.map((p) =>
        confirming === p.source ? (
          <div key={p.source} className="py-1.5">
            <InlineConfirm
              question={`Remove ${p.source}?`}
              consequence="piglet stops loading it after /reload, and a package cloned from git is deleted."
              confirmLabel="Remove"
              busyLabel="Removing…"
              failure="Couldn't remove the package."
              onConfirm={async () => {
                await request("remove_package", { source: p.source });
                setConfirming(null);
                await load();
              }}
              onCancel={() => setConfirming(null)}
            />
          </div>
        ) : (
          <div key={p.source} className="flex items-center gap-4 py-1 pointer-coarse:min-h-11">
            <span className="min-w-0 flex-1 font-mono text-[13px] [overflow-wrap:anywhere]">
              {p.source}
            </span>
            <Button
              variant="danger"
              aria-label={`Remove ${p.source}`}
              onClick={() => setConfirming(p.source)}
            >
              Remove
            </Button>
          </div>
        ),
      )}
      <InstallPackage onInstalled={() => void load()} />
    </Section>
  );
}

function InstallPackage({ onInstalled }: { onInstalled: () => void }) {
  const [source, setSource] = useState("");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SaveStatus>({ kind: "idle" });
  const [done, setDone] = useState(false);
  const submit = async () => {
    const value = source.trim();
    if (!value || busy) return;
    setBusy(true);
    setDone(false);
    setResult({ kind: "idle" });
    try {
      await request("install_package", { source: value });
      setSource("");
      setDone(true);
      onInstalled();
    } catch (err) {
      setResult({ kind: "invalid", message: `Couldn't install. ${reasonText(err)}` });
    } finally {
      setBusy(false);
    }
  };
  return (
    <form
      className="flex flex-col gap-1.5 py-1.5"
      onSubmit={(event) => {
        event.preventDefault();
        void submit();
      }}
    >
      <label htmlFor="install-package" className="flex flex-col">
        <span className="text-[13px]">Install a package</span>
        <span className="text-[11px] text-fg-muted">A git URL or a local folder</span>
      </label>
      <div className="flex gap-2 max-sm:flex-col">
        <input
          id="install-package"
          className={`${inputClass} font-mono`}
          placeholder="git URL or local path"
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          enterKeyHint="go"
          value={source}
          disabled={busy}
          onChange={(event) => setSource(event.target.value)}
        />
        <Button type="submit" variant="primary" disabled={!source.trim() || busy}>
          {busy ? "Installing…" : "Install"}
        </Button>
      </div>
      {done ? (
        <p role="status" className="text-[11px] text-add-fg">
          Installed. Use /reload to load it.
        </p>
      ) : (
        <StatusText status={result} />
      )}
    </form>
  );
}

function Project({ data, reload }: { data: SettingsData; reload: () => Promise<void> }) {
  const cwd = activeView()?.state.cwd;
  if (!cwd)
    return (
      <p className="text-[13px] text-fg-muted">Open a session to edit its project settings.</p>
    );
  return (
    <>
      <p className="mb-1 text-[12px] text-fg-muted [overflow-wrap:anywhere]">
        {data.projectPath} is merged over the global settings and loaded only when the project is
        trusted.
      </p>
      <FileEditor
        label="Project settings"
        initial={JSON.stringify(data.project ?? {}, null, 2)}
        saveLabel="Save project settings"
        heightClass="h-64"
        onSave={async (next) => {
          const parsed = JSON.parse(next.trim() || "{}");
          await request("set_project_settings", { cwd, settings: parsed });
          await reload();
        }}
      />
    </>
  );
}

// Arrow keys (either axis, since the tabs form a row on phones), Home and End move between tabs
// and select them; Tab moves on to the panel.
const TAB_KEYS: Record<string, (index: number) => number> = {
  ArrowDown: (i) => i + 1,
  ArrowRight: (i) => i + 1,
  ArrowUp: (i) => i - 1,
  ArrowLeft: (i) => i - 1,
  Home: () => 0,
  End: () => TABS.length - 1,
};

function onTabKey(event: KeyboardEvent<HTMLButtonElement>) {
  const move = TAB_KEYS[event.key];
  if (!move) return;
  event.preventDefault();
  const index = TABS.findIndex(([id]) => `settings-tab-${id}` === event.currentTarget.id);
  const next = TABS[(move(index) + TABS.length) % TABS.length]?.[0];
  if (!next) return;
  ui.open("settings", next);
  document.getElementById(`settings-tab-${next}`)?.focus();
}

/** Room the phone tab row leaves at its right edge, where it fades out. */
const FADE_PX = 48;

/** Scrolls the phone tab row (only that row) so the selected tab is fully in view. */
function revealTab(list: HTMLElement | null, tab: HTMLElement | null) {
  if (!list || !tab || list.scrollWidth <= list.clientWidth) return;
  const box = list.getBoundingClientRect();
  const rect = tab.getBoundingClientRect();
  if (rect.left < box.left) list.scrollLeft -= box.left - rect.left + 12;
  else if (rect.right > box.right - FADE_PX) list.scrollLeft += rect.right - (box.right - FADE_PX);
}

export function SettingsDialog() {
  const open = useApp((s) => s.dialog === "settings");
  const tab = useApp((s) => s.settingsTab);
  const narrow = useMediaQuery(NARROW);
  const { data, error, patch, load } = useSettings();
  const escapeRef: EscapeRef = useRef(null);
  const tablist = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  // Saves made by any row show once, in the header, so rows never shift when one is saved.
  const { reporter, indicator, reset } = useDialogSaveStatus();
  useEffect(() => {
    if (open) void load();
    else reset();
  }, [open, load, reset]);
  // Each tab starts at its top, and on phones the tab row scrolls to show the selected tab.
  useEffect(() => {
    if (!open) return;
    if (panel.current) panel.current.scrollTop = 0;
    revealTab(tablist.current, document.getElementById(`settings-tab-${tab}`));
  }, [open, tab]);
  return (
    <Modal
      open={open}
      onClose={ui.close}
      title="Settings"
      status={indicator}
      width="max-w-3xl"
      className="sm:h-[min(640px,80dvh)] max-sm:h-[calc(100dvh-1rem-var(--titlebar-bottom))]"
      bodyClassName="flex overflow-hidden max-sm:flex-col"
      onEscapeKeyDown={(event) => {
        // Esc in a field with unsaved text reverts the field; the dialog stays open.
        const active = document.activeElement;
        if (active instanceof HTMLElement && active.dataset.dirty === "true") {
          event.preventDefault();
          return;
        }
        if (escapeRef.current?.()) event.preventDefault();
      }}
    >
      <SaveReporterContext.Provider value={reporter}>
        <div
          ref={tablist}
          role="tablist"
          aria-label="Settings sections"
          aria-orientation={narrow ? "horizontal" : "vertical"}
          className="flex w-40 shrink-0 flex-col gap-0.5 overflow-y-auto py-4 pr-1 pl-3 max-sm:w-full max-sm:flex-row max-sm:gap-1 max-sm:overflow-x-auto max-sm:overflow-y-hidden max-sm:border-b max-sm:border-line max-sm:px-3 max-sm:py-1.5 max-sm:[mask-image:linear-gradient(to_right,#000_85%,transparent)] max-sm:[scrollbar-width:none] max-sm:after:w-12 max-sm:after:shrink-0 max-sm:after:content-['']"
        >
          {TABS.map(([id, label]) => (
            <button
              type="button"
              key={id}
              id={`settings-tab-${id}`}
              role="tab"
              aria-selected={tab === id}
              aria-controls="settings-panel"
              tabIndex={tab === id ? 0 : -1}
              onClick={() => ui.open("settings", id)}
              onKeyDown={onTabKey}
              className={`h-7 shrink-0 whitespace-nowrap rounded-md px-2 text-left text-[13px] focus-visible:outline-offset-[-2px] max-sm:h-9 max-sm:px-3 ${tab === id ? "bg-muted text-fg" : "text-fg-muted hover:text-fg"}`}
            >
              {label}
            </button>
          ))}
        </div>
        <div
          ref={panel}
          role="tabpanel"
          id="settings-panel"
          aria-labelledby={`settings-tab-${tab}`}
          data-settings-scroll
          className="min-h-0 min-w-0 flex-1 overflow-y-auto p-4 sm:pt-5 sm:pr-6 sm:pl-5"
        >
          <div className="max-w-[560px]">
            {!data ? (
              error ? (
                <div role="alert" className="space-y-2 text-[13px]">
                  <p>Couldn't load settings. {error}</p>
                  <Button onClick={() => void load()}>Retry</Button>
                </div>
              ) : (
                <p className="text-[13px] text-fg-muted">Loading settings…</p>
              )
            ) : tab === "general" ? (
              <General data={data} patch={patch} />
            ) : tab === "models" ? (
              <Models data={data} patch={patch} />
            ) : tab === "providers" ? (
              <Providers escapeRef={escapeRef} />
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
      </SaveReporterContext.Provider>
    </Modal>
  );
}
