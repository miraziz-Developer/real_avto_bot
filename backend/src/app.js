import express from "express";
import { createCorsMiddleware } from "./cors.js";
import { router } from "./routes.js";
import { errorHandler, securityHeaders } from "./middleware.js";

export function createApp() {
  const app = express();
  app.disable("x-powered-by");
  // Faqat bitta ishonchli proksi (frontend nginx) — X-Forwarded-For shundan olinadi.
  app.set("trust proxy", 1);
  app.use(securityHeaders);
  app.use(createCorsMiddleware());
  app.use(express.json({ limit: "512kb" }));
  app.use(router);
  app.use(errorHandler);
  return app;
}
