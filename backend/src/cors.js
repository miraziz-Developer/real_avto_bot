import cors from "cors";
import { config } from "./config.js";

/** Ishlab chiqarishda CORS_ORIGIN ni aniq ko‘rsating (vergul bilan bir nechta). */
export function createCorsMiddleware() {
  if (!config.isProduction) {
    return cors();
  }
  const raw = config.corsOrigin;
  if (!raw || raw === "*") {
    console.warn(
      "[WARN] NODE_ENV=production: CORS_ORIGIN bo‘sh yoki * — barcha saytlarga API ochiq. "
        + "Masalan: CORS_ORIGIN=https://crm.sizning-domen.uz",
    );
    return cors();
  }
  const allowed = new Set(raw.split(",").map((s) => s.trim()).filter(Boolean));
  return cors({
    origin(origin, cb) {
      if (!origin) return cb(null, true);
      cb(null, allowed.has(origin));
    },
    credentials: true,
  });
}
