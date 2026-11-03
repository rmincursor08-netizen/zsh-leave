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
    if (originalDocument) {
      Object.defineProperty(globalThis, "document", originalDocument);
    } else {
      Reflect.deleteProperty(globalThis, "document");
    }
  }
}

test("activation API sends token only to the activation endpoint with CSRF", async () => {
  await withMockedFetch(200, { message_fa: "activated" }, async (calls) => {
    await httpApi.activateAccount("raw-token", "person@example.test", "Strong-password-2026");

    assert.equal(calls.length, 1);
    assert.equal(calls[0].url, "/api/v1/auth/activate");
    assert.equal(calls[0].init.credentials, "include");
    const headers = new Headers(calls[0].init.headers);
    assert.equal(headers.get("X-CSRFToken"), "csrf-token");
    assert.deepEqual(JSON.parse(String(calls[0].init.body)), {
      token: "raw-token",
      email: "person@example.test",
      password: "Strong-password-2026",
    });
  });
});

test("pending invitation is represented as pending, not as successful delivery", async () => {
  await withMockedFetch(503, {
    status: "pending",
    message_fa: "delivery unavailable",
    developmentActivationUrl: "http://localhost:5173/activate-account#token=dev-token",
  }, async () => {
    const result = await httpApi.inviteEmployee(17, "person@example.test");
    assert.equal(result.status, "pending");
    assert.equal(result.message_fa, "delivery unavailable");
    assert.match(result.developmentActivationUrl ?? "", /#token=dev-token$/);
  });
});

test("invitation management surfaces server authorization and rate-limit failures", async () => {
  for (const status of [400, 403, 409, 429]) {
    await withMockedFetch(status, {
      code: `http_${status}`,
      message_fa: "request rejected",
    }, async () => {
      await assert.rejects(
        httpApi.revokeEmployeeInvitation(17, "Approved reason"),
        (error: unknown) => {
          assert.equal((error as { status?: number }).status, status);
          return true;
        },
      );
    });
  }
});
