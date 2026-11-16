// WebSocket client: authenticates with the per-launch token, correlates responses and fans out
// event frames. Reconnects automatically; sessions keep running on the server meanwhile.

import type { CommandMap, CommandType } from "./commands";
import type { EventFrame, ResponseFrame } from "./types";

export type ConnectionStatus = "connecting" | "open" | "closed" | "unauthorized";

export interface HelloInfo {
  version: string;
  protocol: number;
  platform: string;
  agentDir: string;
  home: string;
}

type Pending = { resolve: (value: unknown) => void; reject: (error: Error) => void };

export class ProtocolError extends Error {}

export class PigletClient {
  private socket: WebSocket | null = null;
  private pending = new Map<string, Pending>();
  private counter = 0;
  private eventListeners = new Set<(frame: EventFrame) => void>();
  private statusListeners = new Set<(status: ConnectionStatus, hello?: HelloInfo) => void>();
  private retryDelay = 500;
  private closedByUser = false;
  status: ConnectionStatus = "closed";

  constructor(
    private readonly url: string,
    private readonly token: string,
    private readonly createSocket: (url: string) => WebSocket = (u) => new WebSocket(u),
  ) {}

  connect(): void {
    this.closedByUser = false;
    this.setStatus("connecting");
    const socket = this.createSocket(this.url);
    this.socket = socket;
    socket.onopen = () => {
      this.send({ id: "hello", type: "hello", token: this.token });
    };
    socket.onmessage = (message) => this.onFrame(String(message.data));
    socket.onclose = () => {
      this.failPending("Connection closed");
      if (this.status === "unauthorized" || this.closedByUser) return;
      this.setStatus("closed");
      window.setTimeout(() => this.connect(), this.retryDelay);
      this.retryDelay = Math.min(this.retryDelay * 2, 5000);
    };
  }

  close(): void {
    this.closedByUser = true;
    this.socket?.close();
  }

  onEvent(listener: (frame: EventFrame) => void): () => void {
    this.eventListeners.add(listener);
    return () => this.eventListeners.delete(listener);
  }

  onStatus(listener: (status: ConnectionStatus, hello?: HelloInfo) => void): () => void {
    this.statusListeners.add(listener);
    return () => this.statusListeners.delete(listener);
  }

  request<T = unknown, K extends CommandType = CommandType>(
    type: K,
    params: CommandMap[K],
  ): Promise<T> {
    if (this.status !== "open") return Promise.reject(new ProtocolError("Not connected"));
    this.counter += 1;
    const id = `r${this.counter}`;
    return new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject });
      this.send({ ...params, id, type });
    });
  }

  private send(frame: Record<string, unknown>): void {
    this.socket?.send(JSON.stringify(frame));
  }

  private onFrame(text: string): void {
    let frame: ResponseFrame | EventFrame;
    try {
      frame = JSON.parse(text);
    } catch {
      return;
    }
    if (frame.type === "event") {
      for (const listener of this.eventListeners) listener(frame);
      return;
    }
    if (frame.command === "hello") {
      if (frame.success) {
        this.retryDelay = 500;
        this.setStatus("open", frame.data as HelloInfo);
      } else {
        this.setStatus("unauthorized");
      }
      return;
    }
    const pending = frame.id ? this.pending.get(frame.id) : undefined;
    if (!pending || !frame.id) return;
    this.pending.delete(frame.id);
    if (frame.success) pending.resolve(frame.data);
    else pending.reject(new ProtocolError(frame.error ?? "Request failed"));
  }

  private failPending(reason: string): void {
    for (const pending of this.pending.values()) pending.reject(new ProtocolError(reason));
    this.pending.clear();
  }

  private setStatus(status: ConnectionStatus, hello?: HelloInfo): void {
    this.status = status;
    for (const listener of this.statusListeners) listener(status, hello);
  }
}

/** Resolve the server URL and token for web, Electron and Vite dev modes. */
export function connectionParams(): { url: string; token: string } | null {
  const native = window.pigletNative;
  const env = import.meta.env;
  if (env.VITE_PIGLET_URL && env.VITE_PIGLET_TOKEN) {
    return { url: env.VITE_PIGLET_URL, token: env.VITE_PIGLET_TOKEN };
  }
  const url = `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`;
  if (native?.token) return { url, token: native.token };
  const fromHash = new URLSearchParams(location.hash.slice(1)).get("token");
  let token = fromHash;
  try {
    if (fromHash) sessionStorage.setItem("piglet-token", fromHash);
    else token = sessionStorage.getItem("piglet-token");
  } catch {
    // Storage can be unavailable; the hash token still works for this page load.
  }
  if (fromHash) history.replaceState(null, "", location.pathname + location.search);
  return token ? { url, token } : null;
}
