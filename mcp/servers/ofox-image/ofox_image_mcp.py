"""
通过 ofox API 调用 chatgpt-image-2(GPT-Image-2)生图的最小 MCP 服务。
依赖: py -m pip install mcp openai
模型名 / 密钥 / base_url 全部由环境变量控制(见配套的 mcpServers JSON)。
"""
import os
import time
import base64
import pathlib
import urllib.request

from openai import OpenAI
from mcp.server.fastmcp import FastMCP

API_KEY = os.environ.get("OFOX_API_KEY", "")
BASE_URL = os.environ.get("OFOX_BASE_URL", "https://api.ofox.ai/v1")
MODEL = os.environ.get("OFOX_IMAGE_MODEL", "openai/gpt-image-2")
OUT_DIR = pathlib.Path(os.environ.get("OFOX_IMAGE_OUT", "./generated_images"))
OUT_DIR.mkdir(parents=True, exist_ok=True)

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)
mcp = FastMCP("ofox-image")


def _save(data: bytes, idx: int) -> str:
    fp = OUT_DIR / f"{int(time.time())}_{idx}.png"
    fp.write_bytes(data)
    return str(fp.resolve())


def _extract(item) -> bytes:
    b64 = getattr(item, "b64_json", None)
    if b64:
        return base64.b64decode(b64)
    url = getattr(item, "url", None)
    if url:
        return urllib.request.urlopen(url).read()
    raise RuntimeError("接口返回里既没有 b64_json 也没有 url")


@mcp.tool()
def generate_image(
    prompt: str,
    size: str = "1024x1024",
    quality: str = "auto",
    n: int = 1,
) -> str:
    """用 ofox 上的 chatgpt-image-2 文生图，返回保存到本地的 PNG 路径。

    size: 1024x1024 / 1536x1024 / 1024x1536 / auto
    quality: low / medium / high / auto
    n: 生成张数
    """
    resp = client.images.generate(
        model=MODEL, prompt=prompt, size=size, quality=quality, n=n
    )
    paths = [_save(_extract(it), i) for i, it in enumerate(resp.data)]
    return "已生成:\n" + "\n".join(paths)


@mcp.tool()
def edit_image(
    image_path: str,
    prompt: str,
    size: str = "1024x1024",
    mask_path: str = "",
) -> str:
    """图生图/局部重绘：基于已有图片按提示修改。mask_path 可选(透明区域=要重绘的位置)。"""
    images = [open(image_path, "rb")]
    kwargs = dict(model=MODEL, prompt=prompt, size=size, image=images)
    if mask_path:
        kwargs["mask"] = open(mask_path, "rb")
    resp = client.images.edit(**kwargs)
    paths = [_save(_extract(it), i) for i, it in enumerate(resp.data)]
    return "已生成:\n" + "\n".join(paths)


if __name__ == "__main__":
    mcp.run()
