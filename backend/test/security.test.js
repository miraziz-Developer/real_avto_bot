import { test, before, after, beforeEach } from "node:test";
import assert from "node:assert/strict";
import crypto from "node:crypto";
import http from "node:http";

process.env.JWT_SECRET = "x".repeat(40);
process.env.DATABASE_URL = "postgresql://nobody:nothing@127.0.0.1:1/none";

const { hashPassword, verifyPassword, isLegacyHash } = await import("../src/password.js");
const { loginBlockedFor, recordLoginFailure, recordLoginSuccess, _resetLoginLimiter, LOGIN_LIMITS } =
  await import("../src/loginLimiter.js");
const { parseId } = await import("../src/utils.js");
const { insecureConfigProblems } = await import("../src/config.js");
const { signAccessToken, verifyAccessToken } = await import("../src/auth.js");
const { createApp } = await import("../src/app.js");
const { pool } = await import("../src/db.js");

test("scrypt hash: tuzli, tekshiriladi, noto'g'ri parol rad etiladi", async () => {
  const a = await hashPassword("Sir-parol-123");
  const b = await hashPassword("Sir-parol-123");
  assert.notEqual(a, b, "har safar boshqa tuz");
  assert.ok(a.length <= 255);
  assert.equal(isLegacyHash(a), false);
  assert.equal(await verifyPassword("Sir-parol-123", a), true);
  assert.equal(await verifyPassword("sir-parol-123", a), false);
  assert.equal(await verifyPassword("", a), false);
  assert.equal(await verifyPassword("x", ""), false);
  assert.equal(await verifyPassword("x", "scrypt$zz$zz"), false);
});

test("eski sha256 xesh hali ham ishlaydi (migratsiya uchun)", async () => {
  const legacy = crypto.createHash("sha256").update("old-pass").digest("hex");
  assert.equal(isLegacyHash(legacy), true);
  assert.equal(await verifyPassword("old-pass", legacy), true);
  assert.equal(await verifyPassword("other", legacy), false);
});

test("parseId faqat musbat butun sonlarni qabul qiladi", () => {
  assert.equal(parseId("12"), 12);
  for (const bad of ["0", "-1", "12abc", "1e3", "", " 1", "99999999999", undefined]) {
    assert.equal(parseId(bad), null, String(bad));
  }
});

test("insecureConfigProblems: standart sirlar fatal", () => {
  const bad = insecureConfigProblems({ jwtSecret: "change_me_super_secret", adminPassword: "admin123" });
  assert.equal(bad.fatal.length, 2);
  const ok = insecureConfigProblems({ jwtSecret: "a".repeat(64), adminPassword: "Uzun-va-kuchli-parol" });
  assert.deepEqual(ok, { fatal: [], warnings: [] });
  const short = insecureConfigProblems({ jwtSecret: "a".repeat(64), adminPassword: "qisqa1" });
  assert.equal(short.fatal.length, 0);
  assert.equal(short.warnings.length, 1);
});

test("JWT: faqat HS256, boshqa kalit bilan imzolangan token rad etiladi", async () => {
  const t = signAccessToken({ id: 1, role: "admin" });
  assert.equal(verifyAccessToken(t).role, "admin");
  const jwt = (await import("jsonwebtoken")).default;
  const forged = jwt.sign({ id: 1, role: "admin" }, "boshqa-kalit");
  assert.throws(() => verifyAccessToken(forged));
  const none = jwt.sign({ id: 1, role: "admin" }, null, { algorithm: "none" });
  assert.throws(() => verifyAccessToken(none));
});

test("login limiter: IP+username bo'yicha bloklaydi, muvaffaqiyat tozalaydi", () => {
  _resetLoginLimiter();
  for (let i = 0; i < LOGIN_LIMITS.MAX_PER_IP_USER; i++) {
    assert.equal(loginBlockedFor("1.1.1.1", "admin"), 0);
    recordLoginFailure("1.1.1.1", "admin");
  }
  assert.ok(loginBlockedFor("1.1.1.1", "admin") > 0);
  assert.equal(loginBlockedFor("2.2.2.2", "admin"), 0, "boshqa IP bloklanmaydi");
  recordLoginSuccess("1.1.1.1", "admin");
  assert.equal(loginBlockedFor("1.1.1.1", "admin"), 0);
});

let server;
let base;
before(async () => {
  server = http.createServer(createApp());
  await new Promise((r) => server.listen(0, "127.0.0.1", r));
  base = `http://127.0.0.1:${server.address().port}`;
});
after(async () => {
  await new Promise((r) => server.close(r));
  await pool.end();
});
beforeEach(() => _resetLoginLimiter());

test("HTTP: himoyalangan endpointlar tokensiz 401", async () => {
  for (const path of ["/stats", "/clients", "/listings"]) {
    const r = await fetch(base + path);
    assert.equal(r.status, 401, path);
  }
  const r = await fetch(base + "/stats", { headers: { Authorization: "Bearer yaroqsiz" } });
  assert.equal(r.status, 401);
});

test("HTTP: xavfsizlik headerlari va health", async () => {
  const r = await fetch(base + "/health");
  assert.equal(r.status, 200);
  assert.equal(r.headers.get("x-frame-options"), "DENY");
  assert.equal(r.headers.get("x-content-type-options"), "nosniff");
  assert.equal(r.headers.get("x-powered-by"), null);
});

test("HTTP: bo'sh login 401, ko'p urinishdan keyin 429", async () => {
  const post = (body) =>
    fetch(base + "/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  for (let i = 0; i < LOGIN_LIMITS.MAX_PER_IP_USER; i++) {
    const r = await post({ username: "admin", password: "" });
    assert.equal(r.status, 401);
  }
  const r = await post({ username: "admin", password: "" });
  assert.equal(r.status, 429);
  assert.ok(Number(r.headers.get("retry-after")) > 0);
});

test("HTTP: buzilgan JSON 400 qaytaradi (500 emas)", async () => {
  const r = await fetch(base + "/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{buzuq",
  });
  assert.equal(r.status, 400);
});
