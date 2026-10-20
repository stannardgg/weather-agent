"""【M8】一键打包桌面版 exe。

用法：
    .venv/Scripts/python.exe scripts/build_exe.py            # 完整打包
    .venv/Scripts/python.exe scripts/build_exe.py --icon-only  # 只重新生成图标

做四件事：
1. 生成程序图标（如果 `packaging/app.ico` 不存在）。
2. 清理上一次的构建产物（`build/`、`dist/`）。
3. 调 PyInstaller 按 `weather_agent.spec` 打成**单文件** exe。
4. 把产物重命名成中文名，并在旁边放一份简短的使用说明。

## 为什么不在 spec 里直接写中文 exe 名

PyInstaller 会拿 `name` 去拼一堆中间文件名，非 ASCII 名在部分环境下会踩到编码问题
（报错信息还往往指向别处，很难定位）。所以 spec 里用 ASCII 名构建，
构建成功后再重命名——产物的最终名字照样是中文。
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PACKAGING_DIR = PROJECT_ROOT / "packaging"
ICON_PATH = PACKAGING_DIR / "app.ico"
SPEC_PATH = PROJECT_ROOT / "weather_agent.spec"
DIST_DIR = PROJECT_ROOT / "dist"
BUILD_DIR = PROJECT_ROOT / "build"

BUILT_NAME = "GuageWeatherAgent.exe"
FINAL_NAME = "瓜葛天气助手.exe"

USAGE_TEXT = """瓜葛天气助手 —— 使用说明
==================================================

【怎么用】

  双击「瓜葛天气助手.exe」即可。程序会自动完成三件事：
    1. 首次运行时在本目录生成配置文件 .env 与数据目录 data/
    2. 建好数据库
    3. 打开浏览器进入界面

  第一次启动要解包并加载本地向量模型，可能需要 30 秒到 1 分钟，
  请耐心等待控制台窗口打印出「请在浏览器访问」后再操作。

【怎么退出】

  直接关闭那个黑色的控制台窗口即可，后端与界面会一起退出。

【数据在哪】

  全部在 exe 同级的 data/ 目录里（数据库、上传的文档、向量索引），
  整个文件夹拷走就等于完整备份。

【配置怎么改】

  用记事本打开同级的 .env 文件即可修改。常用的几项：
    LLM_API_KEY      大模型密钥（不填则通用问答不可用）
    WEATHER_API_KEY  和风天气密钥（不填则天气查询不可用）
    SECRET_KEY       登录令牌签名密钥，改完需要重新登录
  改完保存并重启程序生效。

【端口被占用怎么办】

  默认后端用 8000、界面用 8501。若被其他软件占用，可在启动前设置环境变量：
    set BACKEND_PORT=18000
    set FRONTEND_PORT=18501
  然后再双击运行。

【打不开 / 报错怎么办】

  控制台窗口里的信息就是排查线索，请保留截图。
"""


def build_icon() -> None:
    """生成程序图标：粉色圆角底 + 白色云朵。

    用代码画而不是塞一张外部图片，是为了让图标能跟着项目一起进版本库、
    不依赖任何二进制素材，重新打包时也不会因为找不到图片而失败。
    """
    from PIL import Image, ImageDraw

    size = 256
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    # 圆角粉色底：竖向渐变靠「一层层画横线」手工实现——Pillow 没有原生渐变填充
    top = (255, 176, 200)
    bottom = (240, 82, 127)
    for y in range(size):
        ratio = y / (size - 1)
        color = tuple(round(top[i] + (bottom[i] - top[i]) * ratio) for i in range(3))
        draw.line([(0, y), (size, y)], fill=(*color, 255))

    # 把渐变裁成圆角矩形：先画好直角矩形，再用圆角遮罩抠出来
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, size - 1, size - 1], radius=56, fill=255)
    image.putalpha(mask)

    # 白色云朵：三个圆 + 一个圆角矩形底，叠在一起就是常见云形
    cloud = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    cd = ImageDraw.Draw(cloud)
    white = (255, 255, 255, 255)
    cd.ellipse([62, 118, 138, 194], fill=white)   # 左圆
    cd.ellipse([108, 88, 200, 180], fill=white)   # 上中圆（最大）
    cd.ellipse([160, 124, 218, 182], fill=white)  # 右圆
    cd.rounded_rectangle([70, 150, 206, 196], radius=23, fill=white)  # 云底
    image = Image.alpha_composite(image, cloud)

    PACKAGING_DIR.mkdir(parents=True, exist_ok=True)
    image.save(ICON_PATH, sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"[图标] 已生成 {ICON_PATH}")


def clean() -> None:
    for path in (BUILD_DIR, DIST_DIR):
        if path.exists():
            shutil.rmtree(path, ignore_errors=True)
            print(f"[清理] 已删除 {path.name}/")


def run_pyinstaller() -> int:
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        str(SPEC_PATH),
        "--noconfirm",
        "--clean",
        "--distpath",
        str(DIST_DIR),
        "--workpath",
        str(BUILD_DIR),
    ]
    print("[打包] " + " ".join(cmd))
    started = time.monotonic()
    code = subprocess.call(cmd, cwd=PROJECT_ROOT)
    print(f"[打包] PyInstaller 退出码 {code}，耗时 {time.monotonic() - started:.0f}s")
    return code


def finalize() -> Path | None:
    built = DIST_DIR / BUILT_NAME
    if not built.exists():
        return None
    final = DIST_DIR / FINAL_NAME
    if final.exists():
        final.unlink()
    built.rename(final)
    (DIST_DIR / "使用说明.txt").write_text(USAGE_TEXT, encoding="utf-8")
    print(f"[产物] {final}  ({final.stat().st_size / 1024 / 1024:.0f} MB)")
    print(f"[产物] {DIST_DIR / '使用说明.txt'}")
    return final


def main() -> int:
    if "--icon-only" in sys.argv:
        build_icon()
        return 0

    if not ICON_PATH.exists():
        build_icon()

    clean()
    if run_pyinstaller() != 0:
        print("[错误] 打包失败，请查看上方 PyInstaller 输出。", file=sys.stderr)
        return 1

    if finalize() is None:
        print(f"[错误] 未找到预期产物 dist/{BUILT_NAME}", file=sys.stderr)
        return 1

    print("\n完成。把 dist 目录里的 exe 与「使用说明.txt」一起拷给别人即可。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
