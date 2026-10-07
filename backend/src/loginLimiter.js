/**
 * Login brute-force himoyasi (jarayon xotirasida; bitta backend instansiya uchun yetarli).
 * IP bo'yicha va IP+username bo'yicha muvaffaqiyatsiz urinishlar sanaladi.
 */

const WINDOW_MS = 15 * 60 * 1000;
const MAX_PER_IP = 30;
const MAX_PER_IP_USER = 8;
const MAX_KEYS = 50_000;

/** key -> { count, first } */
const failures = new Map();

function now() {
  return Date.now();
}

function sweep(t) {
  if (failures.size < MAX_KEYS) return;
  for (const [k, v] of failures) {
    if (t - v.first > WINDOW_MS) failures.delete(k);
  }
  // Hali ham to'la bo'lsa — eng eskilarini tashlash (Map tartibi = qo'shilish tartibi).
  while (failures.size >= MAX_KEYS) {
    failures.delete(failures.keys().next().value);
  }
}

function get(key, t) {
  const v = failures.get(key);
  if (!v) return 0;
  if (t - v.first > WINDOW_MS) {
    failures.delete(key);
    return 0;
  }
  return v.count;
}

function bump(key, t) {
  const v = failures.get(key);
  if (!v || t - v.first > WINDOW_MS) {
    failures.set(key, { count: 1, first: t });
  } else {
    v.count += 1;
  }
}

function keys(ip, username) {
  const u = String(username || "").trim().toLowerCase().slice(0, 80);
  return { ipKey: `ip:${ip}`, userKey: `iu:${ip}:${u}` };
}

/** Bloklangan bo'lsa qolgan soniyalar, aks holda 0. */
export function loginBlockedFor(ip, username) {
  const t = now();
  const { ipKey, userKey } = keys(ip, username);
  const blocked = get(ipKey, t) >= MAX_PER_IP || get(userKey, t) >= MAX_PER_IP_USER;
  if (!blocked) return 0;
  const firsts = [failures.get(ipKey)?.first, failures.get(userKey)?.first].filter(Boolean);
  const oldest = Math.min(...firsts);
  return Math.max(1, Math.ceil((oldest + WINDOW_MS - t) / 1000));
}

export function recordLoginFailure(ip, username) {
  const t = now();
  sweep(t);
  const { ipKey, userKey } = keys(ip, username);
  bump(ipKey, t);
  bump(userKey, t);
}

export function recordLoginSuccess(ip, username) {
  const { userKey } = keys(ip, username);
  failures.delete(userKey);
}

export function _resetLoginLimiter() {
  failures.clear();
}

export const LOGIN_LIMITS = { WINDOW_MS, MAX_PER_IP, MAX_PER_IP_USER };
