# Trae MCP

This directory contains reusable MCP server snippets and a sanitized Trae `mcp.json` example.

## Included servers

- `python-run`: runs user Python code in a child process and collects chart/image outputs.
- `ocr`: local RapidOCR-based OCR for images and scanned PDFs.
- `ofox-image`: image generation/editing through an OpenAI-compatible image API.
- `doubao-seed2-vision-mcp`: local-file-aware vision analysis through Volcengine Ark / Doubao Seed.
- `playwright`: shown only as a config snippet; use Edge with `--browser msedge` on Windows to avoid extra browser downloads.

## Notes

- Keep real API keys out of Git. Put them in local Trae config or environment variables.
- Prefer environment variables for paths and model names.
- After editing `mcp.json` or any server file, restart the corresponding MCP server in Trae.
