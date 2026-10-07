import express from "express";
import { createCorsMiddleware } from "./cors.js";
import { router } from "./routes.js";
import { errorHandler, securityHeaders } from "./middleware.js";

export function createApp() {
  const app = express();
  app.disable("x-powered-by");
  // Caddy/nginx → catalog nginx → backend: barcha ichki (loopback / docker) hoplarga ishonamiz,
  // shunda req.ip haqiqiy mijoz IP si bo'ladi (login va ochiq API rate-limit har mijozga alohida).
  app.set("trust proxy", process.env.TRUST_PROXY || "loopback, linklocal, uniquelocal");
  app.use(securityHeaders);
  app.use(createCorsMiddleware());
  app.use(express.json({ limit: "512kb" }));
  app.use(router);
  app.use(errorHandler);
  return app;
}
