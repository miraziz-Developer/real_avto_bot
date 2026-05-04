import dotenv from "dotenv";

dotenv.config();

const nodeEnv = (process.env.NODE_ENV || "development").trim();

export const config = {
  nodeEnv,
  isProduction: nodeEnv === "production",
  port: Number(process.env.PORT || 3001),
  jwtSecret: process.env.JWT_SECRET || "change_me_super_secret",
  jwtExpiresIn: process.env.JWT_EXPIRES_IN || "12h",
  adminUser: process.env.CRM_ADMIN_USER || "admin",
  adminPassword: process.env.CRM_ADMIN_PASSWORD || "admin123",
  /** Vergul bilan bir nechta origin (CRM domenlari). Bo‘sh bo‘lsa productionda ham ochiq CORS. */
  corsOrigin: (process.env.CORS_ORIGIN || "").trim(),
};
