import crypto from "crypto";
import { promisify } from "util";

const scrypt = promisify(crypto.scrypt);

const KEY_LEN = 32;
const SALT_LEN = 16;
const PREFIX = "scrypt";

/** Parolni tuzli scrypt bilan xeshlash: `scrypt$<salt hex>$<hash hex>` (~104 belgi). */
export async function hashPassword(password) {
  const salt = crypto.randomBytes(SALT_LEN);
  const key = await scrypt(String(password), salt, KEY_LEN);
  return `${PREFIX}$${salt.toString("hex")}$${key.toString("hex")}`;
}

function safeEqualHex(a, b) {
  const ba = Buffer.from(a, "hex");
  const bb = Buffer.from(b, "hex");
  if (ba.length !== bb.length || ba.length === 0) return false;
  return crypto.timingSafeEqual(ba, bb);
}

/** Eski (tuzsiz sha256) xeshmi — muvaffaqiyatli logindan keyin yangilash kerak. */
export function isLegacyHash(stored) {
  return typeof stored === "string" && !stored.startsWith(`${PREFIX}$`);
}

export async function verifyPassword(password, stored) {
  if (typeof stored !== "string" || !stored) return false;
  const pw = String(password ?? "");
  if (isLegacyHash(stored)) {
    const legacy = crypto.createHash("sha256").update(pw).digest("hex");
    return /^[0-9a-f]{64}$/i.test(stored) && safeEqualHex(legacy, stored.toLowerCase());
  }
  const parts = stored.split("$");
  if (parts.length !== 3) return false;
  const [, saltHex, keyHex] = parts;
  const key = await scrypt(pw, Buffer.from(saltHex, "hex"), KEY_LEN);
  return safeEqualHex(key.toString("hex"), keyHex);
}

/** Login timing orqali username mavjudligini bilib olmaslik uchun — foydalanuvchi yo'q bo'lsa ham hisoblash. */
let dummyHash = null;
export async function burnPasswordCheck(password) {
  if (!dummyHash) dummyHash = await hashPassword(crypto.randomBytes(16).toString("hex"));
  await verifyPassword(password, dummyHash);
}
