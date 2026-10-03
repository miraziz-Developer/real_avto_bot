import dotenv from "dotenv";

dotenv.config();

const nodeEnv = (process.env.NODE_ENV || "development").trim();

const DEFAULT_JWT_SECRET = "change_me_super_secret";
const DEFAULT_ADMIN_PASSWORD = "admin123";

export const config = {
  nodeEnv,
  isProduction: nodeEnv === "production",
  port: Number(process.env.PORT || 3001),
  jwtSecret: process.env.JWT_SECRET || DEFAULT_JWT_SECRET,
  jwtExpiresIn: process.env.JWT_EXPIRES_IN || "12h",
  adminUser: (process.env.CRM_ADMIN_USER || "admin").trim(),
  adminPassword: process.env.CRM_ADMIN_PASSWORD || DEFAULT_ADMIN_PASSWORD,
  /** Vergul bilan bir nechta origin (CRM domenlari). Bo‘sh bo‘lsa productionda ham ochiq CORS. */
  corsOrigin: (process.env.CORS_ORIGIN || "").trim(),
};

/**
 * Xavfli sozlamalar. `fatal` bo'sh bo'lmasa productionda backend ishga tushmaydi.
 * @returns {{fatal: string[], warnings: string[]}}
 */
export function insecureConfigProblems(c = config) {
  const fatal = [];
  const warnings = [];
  if (!c.jwtSecret || c.jwtSecret.length < 32 || /change_me/i.test(c.jwtSecret)) {
    fatal.push("JWT_SECRET kamida 32 belgi va standart bo'lmasligi kerak (openssl rand -hex 32)");
  }
  if (!c.adminPassword || c.adminPassword === DEFAULT_ADMIN_PASSWORD) {
    fatal.push("CRM_ADMIN_PASSWORD standart (admin123) yoki bo'sh — almashtiring");
  } else if (c.adminPassword.length < 10) {
    warnings.push("CRM_ADMIN_PASSWORD 10 belgidan qisqa — uzunroq parol tavsiya etiladi");
  }
  return { fatal, warnings };
}
