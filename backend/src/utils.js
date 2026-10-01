import crypto from "crypto";

export function sha256(v) {
  return crypto.createHash("sha256").update(v).digest("hex");
}

/** Parol xeshi: scrypt + tasodifiy tuz. Format: scrypt$<salt hex>$<hash hex> (104 belgi — crm_users.password_hash varchar(128) ga sig'adi) */
export function hashPassword(password) {
  const salt = crypto.randomBytes(16);
  const hash = crypto.scryptSync(String(password), salt, 32);
  return `scrypt$${salt.toString("hex")}$${hash.toString("hex")}`;
}

/** Yangi (scrypt) va eski (tuzsiz sha256 hex) xeshlarni tekshiradi. */
export function verifyPassword(password, stored) {
  if (!stored) return false;
  const pw = String(password || "");
  if (stored.startsWith("scrypt$")) {
    const [, saltHex, hashHex] = stored.split("$");
    const expected = Buffer.from(hashHex || "", "hex");
    const actual = crypto.scryptSync(pw, Buffer.from(saltHex || "", "hex"), expected.length || 32);
    return expected.length > 0 && crypto.timingSafeEqual(actual, expected);
  }
  const legacy = Buffer.from(sha256(pw), "hex");
  const storedBuf = Buffer.from(stored, "hex");
  return storedBuf.length === legacy.length && crypto.timingSafeEqual(storedBuf, legacy);
}

export function asyncHandler(fn) {
  return (req, res, next) => Promise.resolve(fn(req, res, next)).catch(next);
}

/**
 * Request query dan pagination limit/offset chiqaradi.
 * @param {import('express').Request} req
 * @param {{defaultLimit?: number, maxLimit?: number}} opts
 * @returns {{limit: number, offset: number, page: number}}
 */
export function getPagination(req, opts = {}) {
  const defaultLimit = opts.defaultLimit || 20;
  const maxLimit = opts.maxLimit || 100;

  let page = Math.max(1, Number.parseInt(String(req.query.page || "1"), 10) || 1);
  let limit = Math.max(1, Number.parseInt(String(req.query.limit || String(defaultLimit)), 10) || defaultLimit);
  if (limit > maxLimit) limit = maxLimit;

  const offset = (page - 1) * limit;
  return { limit, offset, page };
}
