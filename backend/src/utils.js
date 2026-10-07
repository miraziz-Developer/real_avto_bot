/** Musbat butun ID (yoki null). "12abc", "-1", "1e3" kabi qiymatlar rad etiladi. */
export function parseId(raw) {
  const s = String(raw ?? "");
  if (!/^[1-9][0-9]{0,9}$/.test(s)) return null;
  const n = Number(s);
  return n <= 2_147_483_647 ? n : null;
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
