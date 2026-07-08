# Doubao Seed 2.0 Vision MCP

Zero-dependency local MCP server for Trae or other MCP clients.

It reads a local image file, converts it to a base64 `data:image/...` URL, and sends it to Volcengine Ark / Doubao Seed 2.0 Vision through the OpenAI-compatible Chat Completions API.

## Trae MCP config

Use your real Ark API key and model or endpoint id:

```json
{
  "mcpServers": {
    "doubao-vision": {
      "command": "node",
      "args": [
        "<REPO>\\mcp\\servers\\doubao-seed2-vision-mcp\\server.js"
      ],
      "env": {
        "ARK_API_KEY": "your_ark_api_key",
        "ARK_BASE_URL": "https://ark.cn-beijing.volces.com/api/v3",
        "ARK_MODEL": "doubao-seed-2-0-lite-260428",
        "VISION_ALLOWED_ROOTS": "<WORKSPACE>"
      }
    }
  }
}
```

If your Ark console uses an inference endpoint, set `ARK_MODEL` to the endpoint id, such as `ep-xxxxxxxxxxxxxxxx`, instead of a public model name.

## Tools

- `analyze_local_image`
  - Reads a local image path.
  - Supports `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`, `.bmp`, `.tif`, `.tiff`.
  - Arguments: `path`, optional `prompt`, optional `mime_type`.

- `analyze_image_url`
  - Sends an `http(s)` image URL or `data:image/...` base64 URL.
  - Arguments: `url`, optional `prompt`.

## Environment variables

- `ARK_API_KEY`, `VOLCENGINE_API_KEY`, or `OPENAI_API_KEY`: required.
- `ARK_BASE_URL` or `OPENAI_BASE_URL`: defaults to `https://ark.cn-beijing.volces.com/api/v3`.
- `ARK_MODEL`, `DOUBAO_MODEL`, or `OPENAI_MODEL`: defaults to `doubao-seed-2-0-lite-260428`.
- `VISION_ALLOWED_ROOTS`: semicolon-separated local roots the MCP may read. Defaults to the process working directory.
- `ALLOW_ALL_PATHS=true`: allows reading any local path. Use only if you trust the MCP client.
- `VISION_MAX_IMAGE_BYTES`: defaults to `10485760` bytes.
- `VISION_MAX_TOKENS`: defaults to `1600`.
- `VISION_TEMPERATURE`: defaults to `0.2`.

## Agent instruction

Add this to your Trae agent prompt:

```text
When the user asks you to inspect a local image, screenshot, UI, or visual file path, call doubao-vision.analyze_local_image. Do not send local paths directly to cloud vision APIs.
```
