import assert from "node:assert/strict";
import { test } from "node:test";
import { httpApi } from "../src/api/http";

async function withMockedFetch(
  status: number,
  body: unknown,
  run: (calls: { url: string; init: RequestInit }[]) => Promise<void>,
) {
  const originalFetch = globalThis.fetch;
  const originalDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
  const calls: { url: string; init: RequestInit }[] = [];
  Object.defineProperty(globalThis, "document", {
    configurable: true,
    value: { cookie: "csrftoken=csrf-token" },
  });
  globalThis.fetch = async (input, init = {}) => {
    calls.push({ url: String(input), init });
    return new Response(JSON.stringify(body), {
      status,
      headers: { "Content-Type": "application/json" },
    });
  };
  try {
    await run(calls);
  } finally {
    globalThis.fetch = originalFetch;
    if (originalDocument) Object.defineProperty(globalThis, "document", originalDocument);
    else Reflect.deleteProperty(globalThis, "document");
  }
}

test("LeaveType create and update send the trimmed required reason", async () => {
  for (const [id, status, method, url] of [
    [undefined, 201, "POST", "/api/v1/leave-types/"],
    [7, 200, "PATCH", "/api/v1/leave-types/7/"],
  ] as const) {
    await withMockedFetch(status, { id: id ?? 7 }, async (calls) => {
      await httpApi.saveType({
        ...(id ? { id } : {}),
        key: "annual",
        labelFa: "مرخصی سالانه",
        enabled: false,
        reason: "  تغییر مصوب  ",
      });

      assert.equal(calls.length, 1);
      assert.equal(calls[0].url, url);
      assert.equal(calls[0].init.method, method);
      assert.deepEqual(JSON.parse(String(calls[0].init.body)), {
        key: "annual",
        labelFa: "مرخصی سالانه",
        enabled: false,
        reason: "تغییر مصوب",
      });
    });
  }
});

test("LeaveType mutations expose validation, permission, conflict and throttle errors", async () => {
  for (const status of [400, 403, 409, 429]) {
    await withMockedFetch(status, {
      code: `leave_type_${status}`,
      message_fa: `error ${status}`,
      fields: status === 400 ? { reason: "reason required" } : undefined,
    }, async () => {
      await assert.rejects(
        httpApi.saveType({
          key: "annual",
          labelFa: "مرخصی سالانه",
          reason: "Change reason",
        }),
        (error: unknown) => {
          assert.equal((error as { status?: number }).status, status);
          assert.equal((error as { message?: string }).message, `error ${status}`);
          return true;
        },
      );
    });
  }
});
