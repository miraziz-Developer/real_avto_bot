import assert from "node:assert/strict";
import crypto from "node:crypto";
import test from "node:test";

import { priceInsight, verifyInitData } from "../src/public.js";

const TOKEN = "123456:TEST_TOKEN";

function signedInitData(fields, token = TOKEN) {
  const dataCheck = Object.keys(fields)
    .sort()
    .map((k) => `${k}=${fields[k]}`)
    .join("\n");
  const secret = crypto.createHmac("sha256", "WebAppData").update(token).digest();
  const hash = crypto.createHmac("sha256", secret).update(dataCheck).digest("hex");
  return new URLSearchParams({ ...fields, hash }).toString();
}

const now = () => String(Math.floor(Date.now() / 1000));

test("valid Telegram initData returns the user", () => {
  const init = signedInitData({ auth_date: now(), query_id: "q", user: JSON.stringify({ id: 42, first_name: "Aziz" }) });
  assert.deepEqual(verifyInitData(init, TOKEN), { id: 42, first_name: "Aziz" });
});

test("tampered initData is rejected", () => {
  const init = signedInitData({ auth_date: now(), user: JSON.stringify({ id: 42 }) });
  const tampered = init.replace("42", "43");
  assert.equal(verifyInitData(tampered, TOKEN), null);
});

test("initData signed with another bot token is rejected", () => {
  const init = signedInitData({ auth_date: now(), user: JSON.stringify({ id: 42 }) }, "999:OTHER");
  assert.equal(verifyInitData(init, TOKEN), null);
});

test("expired initData is rejected", () => {
  const old = String(Math.floor(Date.now() / 1000) - 3 * 86400);
  const init = signedInitData({ auth_date: old, user: JSON.stringify({ id: 42 }) });
  assert.equal(verifyInitData(init, TOKEN), null);
});

test("missing data or token is rejected", () => {
  assert.equal(verifyInitData("", TOKEN), null);
  assert.equal(verifyInitData("auth_date=1&user=%7B%7D", TOKEN), null);
  assert.equal(verifyInitData(signedInitData({ auth_date: now() }), ""), null);
});

test("price insight: cheaper / fair / hidden when expensive or too few comparables", () => {
  assert.deepEqual(priceInsight(9200, 10200, 3), { kind: "cheaper", pct: 10, median: 10200, comparables: 3 });
  assert.equal(priceInsight(10000, 10200, 5).kind, "fair");
  assert.equal(priceInsight(12000, 10000, 5), null); // qimmat — ko'rsatilmaydi
  assert.equal(priceInsight(9000, 10000, 2), null); // taqqoslash uchun kam
  assert.equal(priceInsight(null, 10000, 5), null);
});
