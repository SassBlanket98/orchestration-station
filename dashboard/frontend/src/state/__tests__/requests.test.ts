import { describe, expect, it } from "vitest";
import {
  IDLE_REQUEST_STATE,
  type RequestState,
  requestReducer,
  requestView,
} from "../hooks";

const idle = IDLE_REQUEST_STATE as RequestState<string>;

describe("requestView", () => {
  it("reads a new key as loading before the effect has started the request", () => {
    expect(requestView(idle, "a")).toEqual({
      key: "a",
      data: null,
      loading: true,
      error: null,
    });
  });

  it("reads no key as idle", () => {
    expect(requestView(idle, null)).toEqual({
      key: null,
      data: null,
      loading: false,
      error: null,
    });
  });

  it("reads a key that matches the stored state as that state", () => {
    const state: RequestState<string> = {
      key: "a",
      data: "v",
      loading: false,
      error: null,
    };
    expect(requestView(state, "a")).toBe(state);
  });

  it("drops stored data when the key changes", () => {
    const state: RequestState<string> = {
      key: "a",
      data: "v",
      loading: false,
      error: null,
    };
    expect(requestView(state, "b")).toEqual({
      key: "b",
      data: null,
      loading: true,
      error: null,
    });
  });
});

describe("requestReducer", () => {
  it("moves from start to success with the data and no error", () => {
    const started = requestReducer(idle, { type: "start", key: "a" });
    expect(started.loading).toBe(true);

    const done = requestReducer(started, { type: "success", key: "a", data: "v" });
    expect(done).toEqual({ key: "a", data: "v", loading: false, error: null });
  });

  it("moves from start to failure with the error and no data", () => {
    const started = requestReducer(idle, { type: "start", key: "a" });
    const error = new Error("network down");

    const failed = requestReducer(started, { type: "failure", key: "a", error });
    expect(failed).toEqual({ key: "a", data: null, loading: false, error });
  });

  it("reload keeps the previous data, clears the error and is loading again", () => {
    const ok = requestReducer(
      requestReducer(idle, { type: "start", key: "a" }),
      { type: "success", key: "a", data: "v" },
    );
    const reloading = requestReducer(ok, { type: "start", key: "a" });
    expect(reloading).toEqual({ key: "a", data: "v", loading: true, error: null });

    const failed = requestReducer(reloading, {
      type: "failure",
      key: "a",
      error: new Error("boom"),
    });
    const retried = requestReducer(failed, { type: "start", key: "a" });
    expect(retried.error).toBeNull();
    expect(retried.loading).toBe(true);
    expect(retried.data).toBe("v");
  });

  it("a start for a new key clears the old data", () => {
    const ok: RequestState<string> = {
      key: "a",
      data: "old",
      loading: false,
      error: null,
    };
    expect(requestReducer(ok, { type: "start", key: "b" })).toEqual({
      key: "b",
      data: null,
      loading: true,
      error: null,
    });
  });

  it("ignores a result for a key that is no longer current", () => {
    let state = requestReducer(idle, { type: "start", key: "a" });
    state = requestReducer(state, { type: "start", key: "b" });

    const late = requestReducer(state, { type: "success", key: "a", data: "stale" });
    expect(late).toBe(state);
    expect(late.loading).toBe(true);
    expect(late.data).toBeNull();

    const lateError = requestReducer(state, {
      type: "failure",
      key: "a",
      error: new Error("stale"),
    });
    expect(lateError).toBe(state);
  });
});
