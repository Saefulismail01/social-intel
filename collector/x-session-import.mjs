#!/usr/bin/env node
/**
 * Import an X session into the VPS Chrome profile over CDP.
 *
 * Reads cookie values from a local file so they never pass through a command
 * line, a shell history, or a chat transcript. Accepts either a .env-style
 * file or JSON (chmod 600 either way):
 *
 *   auth_token=...            |    {"auth_token": "...", "ct0": "..."}
 *   ct0=...                   |
 *
 * Usage:  node scripts/x-session-import.mjs [path]   (default: .env)
 *
 * Cookie values are never printed; the script reports only whether the
 * resulting session is authenticated.
 */
import { readFileSync } from "node:fs";
import { chromium } from "playwright-core";

const CDP_URL = process.env.SI_CDP_URL ?? "http://127.0.0.1:9222";
const SOURCE = process.argv[2] ?? ".env";

// auth_token carries the session; ct0 is the CSRF token the web app pairs with it.
const REQUIRED = ["auth_token", "ct0"];
const OPTIONAL = ["twid", "kdt", "guest_id", "personalization_id"];

function parseEnv(text) {
  const values = {};
  for (const line of text.split("\n")) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const separator = trimmed.indexOf("=");
    if (separator < 1) continue;
    const key = trimmed.slice(0, separator).trim();
    // Tolerate quoting and an `export ` prefix; cookie values contain no spaces.
    values[key.replace(/^export\s+/, "")] = trimmed.slice(separator + 1).trim().replace(/^["']|["']$/g, "");
  }
  return values;
}

function loadCookies(path) {
  let text;
  try {
    text = readFileSync(path, "utf8");
  } catch (error) {
    throw new Error(`cannot read ${path}: ${error.message}`);
  }
  let raw;
  try {
    raw = JSON.parse(text);
  } catch {
    raw = parseEnv(text);
  }
  const missing = REQUIRED.filter(name => !raw[name]);
  if (missing.length) throw new Error(`missing cookie(s): ${missing.join(", ")}`);

  return [...REQUIRED, ...OPTIONAL]
    .filter(name => raw[name])
    .map(name => ({
      name,
      value: String(raw[name]).trim(),
      domain: ".x.com",
      path: "/",
      secure: true,
      httpOnly: name === "auth_token",
      sameSite: "None",
      expires: Math.floor(Date.now() / 1000) + 60 * 60 * 24 * 365,
    }));
}

const cookies = loadCookies(SOURCE);
const browser = await chromium.connectOverCDP(CDP_URL);
const context = browser.contexts()[0];

// Mirror onto twitter.com too: some endpoints still redirect through it.
await context.addCookies([
  ...cookies,
  ...cookies.map(cookie => ({ ...cookie, domain: ".twitter.com" })),
]);
console.log(`imported ${cookies.length} cookie(s) into the VPS Chrome profile`);

const page = await context.newPage();
let authenticated = false;
let landed = "";
try {
  await page.goto("https://x.com/home", { waitUntil: "domcontentloaded", timeout: 45_000 });
  await page.waitForTimeout(6_000);
  landed = page.url();
  // A live session stays on /home; a dead one bounces to the login flow.
  authenticated = !/\/i\/flow\/login/.test(landed) && /\/home/.test(landed);
} catch (error) {
  console.error(`verification failed: ${error.message.slice(0, 200)}`);
} finally {
  await page.close();
  await browser.close();
}

console.log(`landed on: ${landed || "(navigation failed)"}`);
console.log(`session authenticated: ${authenticated}`);
if (!authenticated) {
  console.error(
    "X may have rejected the session because it moved to a new IP or device. " +
    "Re-copy the cookies right after using x.com locally, or reset the password and sign in here directly."
  );
  process.exit(1);
}
