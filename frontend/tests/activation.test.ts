import assert from "node:assert/strict";
import { test } from "node:test";
import { consumeActivationUrl } from "../src/lib/activation";

test("activation token is extracted from the fragment and removed from the URL", () => {
  const result = consumeActivationUrl(
    "https://leave.example.test/activate-account?campaign=mail#token=secret-token",
  );

  assert.equal(result.token, "secret-token");
  assert.equal(result.cleanUrl, "/activate-account?campaign=mail");
  assert.equal(result.cleanUrl.includes("secret-token"), false);
});

test("activation URL without a token is rejected without preserving a fragment", () => {
  const result = consumeActivationUrl("https://leave.example.test/activate-account#other=value");

  assert.equal(result.token, null);
  assert.equal(result.cleanUrl, "/activate-account");
});
