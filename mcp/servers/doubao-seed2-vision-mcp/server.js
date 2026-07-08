#!/usr/bin/env node
"use strict";

const fs = require("node:fs/promises");
const path = require("node:path");
const readline = require("node:readline");
const { fileURLToPath } = require("node:url");

const SERVER_NAME = "doubao-seed2-vision-mcp";
const SERVER_VERSION = "0.1.0";

const DEFAULT_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3";
const DEFAULT_MODEL = "doubao-seed-2-0-lite-260428";
const DEFAULT_PROMPT = "Describe the image carefully. If it contains text, transcribe the important text. Answer in the user's language when possible.";

const IMAGE_MIME_BY_EXT = new Map([
  [".png", "image/png"],
  [".jpg", "image/jpeg"],
  [".jpeg", "image/jpeg"],
  [".webp", "image/webp"],
  [".gif", "image/gif"],
  [".bmp", "image/bmp"],
  [".tif", "image/tiff"],
  [".tiff", "image/tiff"],
]);

function envBool(name, fallback = false) {
  const value = process.env[name];
  if (value == null || value === "") return fallback;
  return /^(1|true|yes|on)$/i.test(value);
}

function envNumber(name, fallback) {
  const value = Number(process.env[name]);
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

function getApiKey() {
  return process.env.ARK_API_KEY || process.env.VOLCENGINE_API_KEY || process.env.OPENAI_API_KEY || "";
}

function getBaseUrl() {
  return (process.env.ARK_BASE_URL || process.env.OPENAI_BASE_URL || DEFAULT_BASE_URL).replace(/\/+$/, "");
}

function getModel() {
  return process.env.ARK_MODEL || process.env.DOUBAO_MODEL || process.env.OPENAI_MODEL || DEFAULT_MODEL;
}

function getAllowedRoots() {
  if (envBool("ALLOW_ALL_PATHS")) return null;

  const raw = process.env.VISION_ALLOWED_ROOTS || process.cwd();
  return raw
    .split(";")
    .map((item) => item.trim())
    .filter(Boolean)
    .map((item) => path.resolve(item));
}

function assertAllowedPath(filePath) {
  const allowedRoots = getAllowedRoots();
  const resolved = path.resolve(filePath);

  if (!allowedRoots) return resolved;

  const isAllowed = allowedRoots.some((root) => {
    const normalizedRoot = path.resolve(root);
    return resolved === normalizedRoot || resolved.startsWith(normalizedRoot + path.sep);
  });

  if (!isAllowed) {
    throw new Error(`Path is outside allowed roots. Set VISION_ALLOWED_ROOTS or ALLOW_ALL_PATHS=true if needed. Path: ${resolved}`);
  }

  return resolved;
}

function normalizeFilePath(input) {
  if (typeof input !== "string" || input.trim() === "") {
    throw new Error("path must be a non-empty string.");
  }

  const trimmed = input.trim();
  if (/^file:/i.test(trimmed)) {
    return fileURLToPath(trimmed);
  }

  return trimmed;
}

function getMimeType(filePath, override) {
  if (override) return override;
  return IMAGE_MIME_BY_EXT.get(path.extname(filePath).toLowerCase()) || "image/png";
}

async function fileToDataUrl(filePath, mimeTypeOverride) {
  const normalized = normalizeFilePath(filePath);
  const resolved = assertAllowedPath(normalized);
  const stat = await fs.stat(resolved);

  if (!stat.isFile()) {
    throw new Error(`Path is not a file: ${resolved}`);
  }

  const maxBytes = envNumber("VISION_MAX_IMAGE_BYTES", 10 * 1024 * 1024);
  if (stat.size > maxBytes) {
    throw new Error(`Image is too large: ${stat.size} bytes. Limit is ${maxBytes}. Set VISION_MAX_IMAGE_BYTES to raise it.`);
  }

  const bytes = await fs.readFile(resolved);
  const mimeType = getMimeType(resolved, mimeTypeOverride);
  return `data:${mimeType};base64,${bytes.toString("base64")}`;
}

function assertImageUrl(url) {
  if (typeof url !== "string" || url.trim() === "") {
    throw new Error("url must be a non-empty string.");
  }

  const trimmed = url.trim();
  if (/^https?:\/\//i.test(trimmed) || /^data:image\//i.test(trimmed)) {
    return trimmed;
  }

  throw new Error("url must be an http(s) URL or a data:image/... base64 URL.");
}

async function callVisionModel(imageUrl, prompt) {
  const apiKey = getApiKey();
  if (!apiKey) {
    throw new Error("Missing API key. Set ARK_API_KEY, VOLCENGINE_API_KEY, or OPENAI_API_KEY.");
  }

  const body = {
    model: getModel(),
    messages: [
      {
        role: "user",
        content: [
          {
            type: "text",
            text: prompt || DEFAULT_PROMPT,
          },
          {
            type: "image_url",
            image_url: {
              url: imageUrl,
            },
          },
        ],
      },
    ],
    temperature: envNumber("VISION_TEMPERATURE", 0.2),
    max_tokens: envNumber("VISION_MAX_TOKENS", 1600),
  };

  const response = await fetch(`${getBaseUrl()}/chat/completions`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${apiKey}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify(body),
  });

  const responseText = await response.text();
  let payload;
  try {
    payload = responseText ? JSON.parse(responseText) : {};
  } catch {
    payload = { raw: responseText };
  }

  if (!response.ok) {
    const detail = typeof payload === "object" ? JSON.stringify(payload) : String(payload);
    throw new Error(`Vision API error ${response.status}: ${detail}`);
  }

  const content = payload?.choices?.[0]?.message?.content;
  if (typeof content === "string") return content;
  if (content != null) return JSON.stringify(content);

  return JSON.stringify(payload);
}

function listTools() {
  return {
    tools: [
      {
        name: "analyze_local_image",
        description: "Analyze a local image file. The server reads the file locally, converts it to base64, then sends it to Doubao Seed 2.0 Vision through the OpenAI-compatible Ark API.",
        inputSchema: {
          type: "object",
          properties: {
            path: {
              type: "string",
              description: "Local image path, for example ./images/screenshot.png or a file:// URI.",
            },
            prompt: {
              type: "string",
              description: "Optional instruction for what to inspect in the image.",
            },
            mime_type: {
              type: "string",
              description: "Optional MIME type override, for example image/png or image/jpeg.",
            },
          },
          required: ["path"],
        },
      },
      {
        name: "analyze_image_url",
        description: "Analyze an image from an http(s) URL or data:image base64 URL using Doubao Seed 2.0 Vision.",
        inputSchema: {
          type: "object",
          properties: {
            url: {
              type: "string",
              description: "Image URL. Must be http(s) or data:image/... base64.",
            },
            prompt: {
              type: "string",
              description: "Optional instruction for what to inspect in the image.",
            },
          },
          required: ["url"],
        },
      },
    ],
  };
}

async function callTool(name, args = {}) {
  if (name === "analyze_local_image") {
    const dataUrl = await fileToDataUrl(args.path, args.mime_type);
    const text = await callVisionModel(dataUrl, args.prompt);
    return {
      content: [{ type: "text", text }],
    };
  }

  if (name === "analyze_image_url") {
    const imageUrl = assertImageUrl(args.url);
    const text = await callVisionModel(imageUrl, args.prompt);
    return {
      content: [{ type: "text", text }],
    };
  }

  throw new Error(`Unknown tool: ${name}`);
}

async function handleRequest(message) {
  const { id, method, params } = message;

  switch (method) {
    case "initialize":
      return {
        protocolVersion: params?.protocolVersion || "2024-11-05",
        capabilities: {
          tools: {},
        },
        serverInfo: {
          name: SERVER_NAME,
          version: SERVER_VERSION,
        },
        instructions: "Use analyze_local_image for local image paths. Use analyze_image_url for public image URLs.",
      };

    case "ping":
      return {};

    case "tools/list":
      return listTools();

    case "tools/call":
      return await callTool(params?.name, params?.arguments || {});

    case "resources/list":
      return { resources: [] };

    case "prompts/list":
      return { prompts: [] };

    case "shutdown":
      return {};

    case "notifications/initialized":
    case "notifications/cancelled":
      return undefined;

    default:
      throw new Error(`Unsupported method: ${method}`);
  }
}

function writeMessage(message) {
  process.stdout.write(`${JSON.stringify(message)}\n`);
}

async function handleMessage(message) {
  if (!message || message.jsonrpc !== "2.0") return;

  const hasId = Object.prototype.hasOwnProperty.call(message, "id");

  try {
    const result = await handleRequest(message);
    if (hasId && result !== undefined) {
      writeMessage({
        jsonrpc: "2.0",
        id: message.id,
        result,
      });
    }
  } catch (error) {
    if (hasId) {
      writeMessage({
        jsonrpc: "2.0",
        id: message.id,
        error: {
          code: -32000,
          message: error instanceof Error ? error.message : String(error),
        },
      });
    }
  }
}

const rl = readline.createInterface({
  input: process.stdin,
  crlfDelay: Infinity,
});

let pendingMessages = 0;
let stdinClosed = false;

function maybeExit() {
  if (stdinClosed && pendingMessages === 0) {
    process.exit(0);
  }
}

rl.on("line", async (line) => {
  pendingMessages += 1;
  const trimmed = line.trim();
  if (!trimmed) {
    pendingMessages -= 1;
    maybeExit();
    return;
  }

  try {
    const parsed = JSON.parse(trimmed);
    if (Array.isArray(parsed)) {
      for (const item of parsed) {
        await handleMessage(item);
      }
    } else {
      await handleMessage(parsed);
    }
  } catch (error) {
    writeMessage({
      jsonrpc: "2.0",
      id: null,
      error: {
        code: -32700,
        message: error instanceof Error ? error.message : String(error),
      },
    });
  } finally {
    pendingMessages -= 1;
    maybeExit();
  }
});

rl.on("close", () => {
  stdinClosed = true;
  maybeExit();
});
