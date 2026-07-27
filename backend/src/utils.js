import crypto from "crypto";

export function sha256(v) {
  return crypto.createHash("sha256").update(v).digest("hex");
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
