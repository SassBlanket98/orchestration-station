// Test double for the browser EventSource. Records every instance so tests can
// assert the shared-connection ref count and drive open/error/event delivery.
export class FakeEventSource {
  static instances: FakeEventSource[] = [];

  static reset(): void {
    FakeEventSource.instances = [];
  }

  static last(): FakeEventSource {
    return FakeEventSource.instances[FakeEventSource.instances.length - 1];
  }

  readonly url: string;
  closed = false;
  onopen: ((ev: Event) => void) | null = null;
  onerror: ((ev: Event) => void) | null = null;
  onmessage: ((ev: MessageEvent) => void) | null = null;
  private listeners = new Map<string, Set<(ev: Event) => void>>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, cb: EventListener): void {
    let set = this.listeners.get(type);
    if (!set) {
      set = new Set();
      this.listeners.set(type, set);
    }
    set.add(cb as (ev: Event) => void);
  }

  removeEventListener(type: string, cb: EventListener): void {
    this.listeners.get(type)?.delete(cb as (ev: Event) => void);
  }

  close(): void {
    this.closed = true;
  }

  fireOpen(): void {
    this.onopen?.(new Event("open"));
  }

  fireError(): void {
    this.onerror?.(new Event("error"));
  }

  emit(type: string, data: string): void {
    const event = { data } as MessageEvent;
    if (type === "message") {
      this.onmessage?.(event);
      return;
    }
    for (const cb of this.listeners.get(type) ?? []) {
      cb(event as unknown as Event);
    }
  }
}
