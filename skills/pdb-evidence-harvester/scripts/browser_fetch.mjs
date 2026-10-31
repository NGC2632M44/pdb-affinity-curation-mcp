import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

async function loadPlaywright() {
  try {
    return await import("playwright");
  } catch {
    const candidates = [
      process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES,
      process.env.RUNTIME_NODE_MODULES,
      path.join(
        os.homedir(),
        ".cache",
        "codex-runtimes",
        "codex-primary-runtime",
        "dependencies",
        "node",
        "node_modules",
      ),
      path.join(process.cwd(), "node_modules"),
    ].filter(Boolean);
    for (const runtimeRoot of candidates) {
      const entry = path.join(runtimeRoot, "playwright", "index.mjs");
      try {
        await fs.access(entry);
        return await import(pathToFileURL(entry).href);
      } catch {
        // Continue through the deterministic runtime candidates.
      }
    }
    throw new Error(
      "playwright is not resolvable; set RUNTIME_NODE_MODULES to a node_modules directory",
    );
  }
}

function argValue(name) {
  const index = process.argv.indexOf(name);
  return index >= 0 ? process.argv[index + 1] : undefined;
}

function allValues(name) {
  const values = [];
  process.argv.forEach((value, index) => {
    if (value === name && process.argv[index + 1]) values.push(process.argv[index + 1]);
  });
  return values;
}

function safeName(value) {
  return value.replace(/[^0-9A-Za-z._-]+/g, "_").slice(0, 120);
}

function blockedReason(url, title, text, hasLoginForm) {
  const combined = `${url}\n${title}\n${text.slice(0, 3000)}`.toLowerCase();
  if (combined.includes("captcha") || combined.includes("recaptcha")) {
    return "captcha_required";
  }
  if (
    combined.includes("access denied") ||
    combined.includes("forbidden") ||
    combined.includes("http 403")
  ) {
    return "access_forbidden";
  }
  if (
    hasLoginForm ||
    combined.includes("sign in to access") ||
    combined.includes("log in to access") ||
    combined.includes("institutional access is required") ||
    combined.includes("purchase access") ||
    combined.includes("subscription required to access")
  ) {
    return "authentication_or_subscription_required";
  }
  return "";
}

const urls = allValues("--url");
const urlFile = argValue("--url-file");
if (urlFile) {
  const fileText = await fs.readFile(urlFile, "utf8");
  for (const line of fileText.split(/\r?\n/)) {
    const value = line.trim();
    if (value && !value.startsWith("#")) urls.push(value);
  }
}
const outputDir = argValue("--output-dir");
if (!urls.length || !outputDir) {
  console.error(
    "Usage: node browser_fetch.mjs --url URL [--url URL] [--url-file FILE] --output-dir DIR",
  );
  process.exit(2);
}

const profileDir = argValue("--profile-dir");
const storageState = argValue("--storage-state");
const saveStorageState = argValue("--save-storage-state");
const maxMb = Number(argValue("--max-mb") || 40);
const waitMs = Number(argValue("--wait-ms") || 2500);
const timeoutMs = Number(argValue("--timeout-ms") || 60000);
const maxLinks = Number(argValue("--max-links") || 12);
const headed = process.argv.includes("--headed");
const downloadLinks = process.argv.includes("--download-links");
const maxBytes = maxMb * 1024 * 1024;
const sleepMs = Number(argValue("--sleep-ms") || 1500);
await fs.mkdir(outputDir, { recursive: true });
await fs.mkdir(path.join(outputDir, "pages"), { recursive: true });
await fs.mkdir(path.join(outputDir, "pdfs"), { recursive: true });

const { chromium } = await loadPlaywright();
const launchOptions = {
  headless: !headed,
  channel: process.env.PLAYWRIGHT_CHANNEL || "msedge",
};
let browser = null;
let context;
if (profileDir) {
  context = await chromium.launchPersistentContext(profileDir, launchOptions);
} else {
  browser = await chromium.launch(launchOptions);
  context = await browser.newContext(storageState ? { storageState } : {});
}
const manifest = [];

function bufferKind(body, url, contentType) {
  const lowerUrl = url.toLowerCase();
  const type = (contentType || "").toLowerCase();
  if (body.length >= 5 && body.subarray(0, 5).toString("latin1") === "%PDF-") {
    return { kind: "pdf", extension: ".pdf" };
  }
  if (body.length >= 4) {
    const head = body.subarray(0, 4).toString("latin1");
    if (head === "II*\u0000" || head === "MM\u0000*") {
      return { kind: "tiff", extension: ".tif" };
    }
    if (head === "PK\u0003\u0004") {
      if (type.includes("word") || /\.docx?(?:$|\?)/i.test(lowerUrl)) {
        return { kind: "docx", extension: ".docx" };
      }
      if (type.includes("excel") || /\.xlsx?(?:$|\?)/i.test(lowerUrl)) {
        return { kind: "xlsx", extension: ".xlsx" };
      }
      return { kind: "zip", extension: ".zip" };
    }
    if (body.subarray(0, 8).equals(Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]))) {
      return { kind: "png", extension: ".png" };
    }
    if (body[0] === 0xff && body[1] === 0xd8) {
      return { kind: "jpeg", extension: ".jpg" };
    }
  }
  if (type.includes("pdf") || /\.pdf(?:$|\?)/i.test(lowerUrl)) {
    return { kind: "invalid_pdf_payload", extension: ".bin" };
  }
  if (type.includes("html") || /\.html?(?:$|\?)/i.test(lowerUrl)) {
    return { kind: "html", extension: ".html" };
  }
  return { kind: "binary", extension: ".bin" };
}

for (const [urlIndex, url] of urls.entries()) {
  const stem = `${String(urlIndex + 1).padStart(3, "0")}_${safeName(url)}`;
  const item = { url, status: "unknown", files: [], links: [] };
  try {
    const request = await context.request.get(url, {
      maxRedirects: 10,
      timeout: timeoutMs,
    });
    const headers = request.headers();
    const contentLength = Number(headers["content-length"] || 0);
    const contentType = headers["content-type"] || "";
    const mightBeFile =
      /(?:pdf|word|officedocument|excel|zip|tiff|image)/i.test(contentType) ||
      /\.(?:pdf|docx?|xlsx?|zip|tiff?)(?:$|\?)/i.test(url);
    if (mightBeFile && contentLength <= maxBytes) {
      const body = await request.body();
      const detected = bufferKind(body, request.url(), contentType);
      if (body.length <= maxBytes && detected.kind !== "invalid_pdf_payload" && detected.kind !== "html") {
        const filePath = path.join(outputDir, "pdfs", `${stem}${detected.extension}`);
        await fs.writeFile(filePath, body);
        item.files.push(filePath);
        item.status = `downloaded_${detected.kind}`;
        item.content_type = contentType;
        item.bytes = body.length;
        item.final_url = request.url();
        manifest.push(item);
        continue;
      }
      if (detected.kind === "invalid_pdf_payload" || detected.kind === "html") {
        item.status = "download_claimed_file_but_returned_nonfile";
        item.content_type = contentType;
        item.warning = `detected_as_${detected.kind}`;
        item.final_url = request.url();
      }
    }

    const page = await context.newPage();
    const response = await page.goto(url, {
      waitUntil: "domcontentloaded",
      timeout: timeoutMs,
    });
    await page.waitForTimeout(waitMs);
    item.http_status = response?.status() ?? null;
    item.final_url = page.url();
    item.title = await page.title();
    const html = await page.content();
    const bodyText = await page.locator("body").innerText().catch(() => "");
    const hasLoginForm = (await page.locator("input[type=password]").count()) > 0;
    const htmlPath = path.join(outputDir, "pages", `${stem}.html`);
    const textPath = path.join(outputDir, "pages", `${stem}.txt`);
    const shotPath = path.join(outputDir, "pages", `${stem}.png`);
    await fs.writeFile(htmlPath, html, "utf8");
    await fs.writeFile(textPath, bodyText, "utf8");
    await page.screenshot({ path: shotPath, fullPage: true });
    item.files.push(htmlPath, textPath, shotPath);
    const links = await page
      .locator("a[href]")
      .evaluateAll((nodes) =>
        [...new Set(nodes.map((node) => node.href).filter(Boolean))],
      );
    item.links = links.filter((href) =>
      /\.(?:pdf|docx?|xlsx?|zip)(?:$|\?)|patent|supplement|supporting/i.test(href),
    );
    const reason =
      item.http_status >= 400
        ? "http_error"
        : blockedReason(item.final_url, item.title, bodyText, hasLoginForm);
    item.status = reason || "html_saved_review_required";

    for (const [index, href] of (downloadLinks ? item.links.slice(0, maxLinks) : []).entries()) {
      const pdfResponse = await context.request.get(href, {
        maxRedirects: 10,
        timeout: timeoutMs,
        headers: { Referer: item.final_url },
      });
      const responseHeaders = pdfResponse.headers();
      const contentType = (responseHeaders["content-type"] || "").toLowerCase();
      const statedLength = Number(responseHeaders["content-length"] || 0);
      if (statedLength > maxBytes) continue;
      const body = await pdfResponse.body();
      if (body.length > maxBytes) continue;
      const detected = bufferKind(body,pdfResponse.url(),contentType);
      if (detected.kind === "invalid_pdf_payload" || detected.kind === "html" || detected.kind === "binary") {
        item.warnings = item.warnings || [];
        item.warnings.push(`attachment_returned_${detected.kind}:${href}`);
        continue;
      }
      const attachmentPath = path.join(
        outputDir,
        "pdfs",
        `${stem}_${index + 1}${detected.extension}`,
      );
      await fs.writeFile(attachmentPath, body);
      item.files.push(attachmentPath);
      await new Promise((resolve) => setTimeout(resolve, sleepMs));
    }
    await page.close();
  } catch (error) {
    item.status = "browser_failed";
    item.error = `${error.name}: ${error.message}`;
  }
  manifest.push(item);
  await new Promise((resolve) => setTimeout(resolve, sleepMs));
}

if (saveStorageState) {
  await context.storageState({ path: saveStorageState });
}
await context.close();
if (browser) await browser.close();
await fs.writeFile(
  path.join(outputDir, "browser_manifest.json"),
  JSON.stringify(manifest, null, 2),
  "utf8",
);
console.log(JSON.stringify(manifest, null, 2));
