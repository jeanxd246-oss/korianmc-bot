/**
 * Panel web de administración de KorianMC Bot.
 *
 * - Autenticación por token (PANEL_TOKEN en el archivo .env de la raíz).
 * - Solo lectura: publica el JSON que exporta el bot en data/dashboard.json.
 * - Arranque:  cd web && npm install && npm start   (puerto PANEL_PORT, por defecto 3000)
 */

const crypto = require("crypto");
const fs = require("fs");
const path = require("path");

const express = require("express");

const ROOT = path.resolve(__dirname, "..");
const DASHBOARD_FILE = path.join(ROOT, "data", "dashboard.json");
const ENV_FILE = path.join(ROOT, ".env");

function loadEnvFile() {
  const values = {};
  try {
    const raw = fs.readFileSync(ENV_FILE, "utf8");
    for (const line of raw.split(/\r?\n/)) {
      const match = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/i);
      if (!match) continue;
      values[match[1]] = match[2].replace(/^["']|["']$/g, "");
    }
  } catch (error) {
    console.warn("[panel] No se pudo leer .env, se usan variables de entorno.");
  }
  return values;
}

const envFile = loadEnvFile();
const PORT = Number(process.env.PANEL_PORT || envFile.PANEL_PORT || 3000);
const TOKEN = String(process.env.PANEL_TOKEN || envFile.PANEL_TOKEN || "");
const BOT_NAME = envFile.BOT_NAME || "KorianMC Bot";

if (!TOKEN || TOKEN === "cambia_este_token_largo_y_secreto") {
  console.error(
    "[panel] Falta PANEL_TOKEN en el archivo .env (o sigue con el valor por defecto). " +
      "Define un token largo y vuelve a arrancar el panel."
  );
  process.exit(1);
}

const app = express();
app.disable("x-powered-by");
app.use(express.json({ limit: "32kb" }));

/** Sesiones en memoria: id -> timestamp de creación (caducan en 8 horas). */
const sessions = new Map();
const SESSION_TTL = 8 * 60 * 60 * 1000;

function pruneSessions() {
  const now = Date.now();
  for (const [id, created] of sessions) {
    if (now - created > SESSION_TTL) sessions.delete(id);
  }
}

function sessionFromRequest(req) {
  const header = req.headers.cookie || "";
  const match = header.match(/(?:^|;\s*)panel_session=([a-f0-9]{32})/);
  if (!match) return null;
  pruneSessions();
  return sessions.has(match[1]) ? match[1] : null;
}

function requireAuth(req, res, next) {
  if (sessionFromRequest(req)) return next();
  res.status(401).json({ error: "Sesión no válida. Vuelve a iniciar sesión." });
}

function tokensMatch(provided, expected) {
  const a = Buffer.from(String(provided));
  const b = Buffer.from(String(expected));
  if (a.length !== b.length) return false;
  return crypto.timingSafeEqual(a, b);
}

// ---------------------------------------------------------------------------
// API
// ---------------------------------------------------------------------------
app.get("/api/health", (_req, res) => {
  res.json({ ok: true, bot: BOT_NAME });
});

app.post("/api/login", (req, res) => {
  const provided = req.body && req.body.token;
  if (!provided || !tokensMatch(provided, TOKEN)) {
    return res.status(401).json({ error: "Token incorrecto." });
  }
  pruneSessions();
  const id = crypto.randomBytes(16).toString("hex");
  sessions.set(id, Date.now());
  res.setHeader(
    "Set-Cookie",
    `panel_session=${id}; HttpOnly; Path=/; Max-Age=${SESSION_TTL / 1000}; SameSite=Strict`
  );
  return res.json({ ok: true });
});

app.post("/api/logout", (req, res) => {
  const header = req.headers.cookie || "";
  const match = header.match(/(?:^|;\s*)panel_session=([a-f0-9]{32})/);
  if (match) sessions.delete(match[1]);
  res.setHeader("Set-Cookie", "panel_session=; HttpOnly; Path=/; Max-Age=0; SameSite=Strict");
  res.json({ ok: true });
});

app.get("/api/data", requireAuth, (_req, res) => {
  fs.readFile(DASHBOARD_FILE, "utf8", (error, raw) => {
    if (error) {
      return res.status(503).json({
        error:
          "Aún no hay datos. Arranca el bot con PANEL_ENABLED=true para que exporte data/dashboard.json.",
      });
    }
    try {
      return res.json(JSON.parse(raw));
    } catch (parseError) {
      return res.status(500).json({ error: "El archivo de datos está corrupto." });
    }
  });
});

app.use(express.static(path.join(__dirname, "public"), { extensions: ["html"] }));

app.listen(PORT, "127.0.0.1", () => {
  console.log(`[panel] ${BOT_NAME} · http://127.0.0.1:${PORT}`);
});
