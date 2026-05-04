import crypto from "crypto";

export function sha256(v) {
  return crypto.createHash("sha256").update(v).digest("hex");
}

export function asyncHandler(fn) {
  return (req, res, next) => Promise.resolve(fn(req, res, next)).catch(next);
}
