import http from "http";
import { config, insecureConfigProblems } from "./config.js";
import { createApp } from "./app.js";
import { bootstrapDatabase } from "./bootstrap.js";
import { pool } from "./db.js";

const { fatal, warnings } = insecureConfigProblems();
for (const w of warnings) console.warn(`[WARN] ${w}`);
if (fatal.length) {
  if (config.isProduction) {
    for (const p of fatal) console.error(`[FATAL] ${p}`);
    console.error("[FATAL] NODE_ENV=production: xavfsiz bo'lmagan sozlamalar bilan ishga tushirilmaydi (backend/.env).");
    process.exit(1);
  }
  for (const p of fatal) console.warn(`[WARN] ${p} (productionda backend ishga tushmaydi)`);
}

await bootstrapDatabase();

const app = createApp();
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
