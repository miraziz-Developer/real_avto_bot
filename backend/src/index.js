import http from "http";
import express from "express";
import { config } from "./config.js";
import { createCorsMiddleware } from "./cors.js";
import { router } from "./routes.js";
import { bootstrapDatabase } from "./bootstrap.js";
import { errorHandler } from "./middleware.js";
import { pool } from "./db.js";

const app = express();
app.disable("x-powered-by");
// Caddy/nginx → catalog nginx → backend: barcha ichki (loopback / docker) hoplarga ishonamiz,
// shunda req.ip haqiqiy mijoz IP si bo'ladi (ochiq API rate-limit har mijozga alohida)
app.set("trust proxy", process.env.TRUST_PROXY || "loopback, linklocal, uniquelocal");
app.use(createCorsMiddleware());
app.use(express.json({ limit: "512kb" }));
app.use(router);
app.use(errorHandler);

await bootstrapDatabase();

if (config.isProduction) {
  const weakJwt =
    !config.jwtSecret ||
    config.jwtSecret.length < 24 ||
    /change_me/i.test(config.jwtSecret);
  if (weakJwt) {
    console.warn("[WARN] JWT_SECRET juda qisqa yoki standart — ishlab chiqarishda almashtiring.");
  }
  if (!config.adminPassword || config.adminPassword === "admin123") {
    console.warn("[WARN] CRM_ADMIN_PASSWORD standart — darhol o‘zgartiring.");
  }
}

const server = http.createServer(app);
server.requestTimeout = 120_000;
server.headersTimeout = 125_000;
server.keepAliveTimeout = 65_000;

server.listen(config.port, () => {
  console.log(`Backend listening on :${config.port}`);
});

async function shutdown(signal) {
  console.info(`Shutdown (${signal})...`);
  await new Promise((resolve, reject) => {
    server.close((err) => (err ? reject(err) : resolve()));
  });
  await pool.end();
  process.exit(0);
}

process.once("SIGTERM", () => {
  shutdown("SIGTERM").catch((e) => {
    console.error(e);
    process.exit(1);
  });
});
process.once("SIGINT", () => {
  shutdown("SIGINT").catch((e) => {
    console.error(e);
    process.exit(1);
  });
});
