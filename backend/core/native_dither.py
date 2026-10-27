"""ctypes bridge for native e-ink dithering."""
from __future__ import annotations

import ctypes
import logging
from pathlib import Path

from PIL import Image

from .config import EINK_4COLOR_PALETTE

import platform

logger = logging.getLogger(__name__)

_EXT = ".dll" if platform.system() == "Windows" else ".so"
_LIB_PATH = Path(__file__).resolve().parent / "native" / f"libeink_dither{_EXT}"
_LIB: ctypes.CDLL | None = None
_BUILD_HINT = "run `python3 backend/scripts/build_native_dither.py` from the repository root"

# Atkinson 抖动核（与 core/native/eink_dither.cpp 的 kKernel 保持一致）：
# (dx, dy, 权重分子)，误差按 权重/8 分发给 6 个邻域像素。
_ATKINSON_KERNEL = (
    (1, 0, 1),
    (2, 0, 1),
    (-1, 1, 1),
    (0, 1, 1),
    (1, 1, 1),
    (0, 2, 1),
)

# 调色板按 index 取 RGB，顺序与 C++ 的 kPalette 一致。
_PALETTE_RGB = tuple(
    tuple(EINK_4COLOR_PALETTE[i * 3: i * 3 + 3]) for i in range(4)
)

_warned_missing_lib = False


def _load_lib() -> ctypes.CDLL | None:
    """加载 native 抖动库。

    库不存在或加载失败时返回 None —— 调用方自动改用纯 Python 实现，
    保证没有编译环境（无 g++）的机器也能正常出图而不是 500。
    """
    global _LIB, _warned_missing_lib
    if _LIB is not None:
        return _LIB
    if not _LIB_PATH.exists():
        if not _warned_missing_lib:
            logger.warning(
                "native dithering library not found at %s (%s); using pure-Python fallback",
                _LIB_PATH, _BUILD_HINT,
            )
            _warned_missing_lib = True
        return None
    try:
        lib = ctypes.CDLL(str(_LIB_PATH))
        lib.inksight_atkinson_bw.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_int,
        ]
        lib.inksight_atkinson_bw.restype = ctypes.c_int
        lib.inksight_atkinson_palette.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_char_p,
            ctypes.c_int,
        ]
        lib.inksight_atkinson_palette.restype = ctypes.c_int
        _LIB = lib
        return lib
    except OSError as exc:
        logger.warning(
            "failed to load native dithering library at %s: %s; using pure-Python fallback",
            _LIB_PATH, exc,
        )
        return None


def _atkinson_bw_python(gray: Image.Image) -> Image.Image:
    """纯 Python 版 Atkinson 黑白抖动，算法与 C++ 实现一致。"""
    src = gray.convert("L")
    w, h = src.size
    data = [float(v) for v in src.tobytes()]
    out = bytearray(w * h)
    for y in range(h):
        row = y * w
        for x in range(w):
            pos = row + x
            old_value = data[pos]
            new_value = 255.0 if old_value >= 128.0 else 0.0
            out[pos] = int(new_value)
            err = old_value - new_value
            if err == 0.0:
                continue
            for dx, dy, weight in _ATKINSON_KERNEL:
                nx = x + dx
                ny = y + dy
                if nx < 0 or nx >= w or ny < 0 or ny >= h:
                    continue
                npos = ny * w + nx
                value = data[npos] + err * (weight / 8.0)
                data[npos] = 0.0 if value < 0.0 else (255.0 if value > 255.0 else value)
    return Image.frombytes("L", (w, h), bytes(out)).convert("1", dither=Image.Dither.NONE)


def _atkinson_palette_python(rgb: Image.Image, colors: int) -> Image.Image:
    """纯 Python 版 Atkinson 调色板抖动，算法与 C++ 实现一致。"""
    if colors not in (3, 4):
        raise ValueError("palette Atkinson dithering supports only 3 or 4 colors")
    src = rgb.convert("RGB")
    w, h = src.size
    data = [float(v) for v in src.tobytes()]
    out = bytearray(w * h)
    allowed = (0, 1, 3) if colors == 3 else (0, 1, 2, 3)
    for y in range(h):
        row = y * w
        for x in range(w):
            pos = row + x
            base = pos * 3
            pr, pg, pb = data[base], data[base + 1], data[base + 2]
            best = allowed[0]
            best_dist = 1e30
            for idx in allowed:
                cr, cg, cb = _PALETTE_RGB[idx]
                dr = pr - cr
                dg = pg - cg
                db = pb - cb
                dist = dr * dr + dg * dg + db * db
                if dist < best_dist:
                    best_dist = dist
                    best = idx
            out[pos] = best
            cr, cg, cb = _PALETTE_RGB[best]
            errs = (pr - cr, pg - cg, pb - cb)
            for dx, dy, weight in _ATKINSON_KERNEL:
                nx = x + dx
                ny = y + dy
                if nx < 0 or nx >= w or ny < 0 or ny >= h:
                    continue
                nbase = (ny * w + nx) * 3
                factor = weight / 8.0
                for channel in range(3):
                    value = data[nbase + channel] + errs[channel] * factor
                    data[nbase + channel] = 0.0 if value < 0.0 else (255.0 if value > 255.0 else value)
    img = Image.frombytes("P", (w, h), bytes(out))
    img.putpalette(EINK_4COLOR_PALETTE + [0] * (768 - len(EINK_4COLOR_PALETTE)))
    return img

def atkinson_bw(gray: Image.Image) -> Image.Image:
    lib = _load_lib()
    if lib is None:
        return _atkinson_bw_python(gray)
    src = gray.convert("L")
    w, h = src.size
    in_buf = src.tobytes()
    out_buf = ctypes.create_string_buffer(w * h)
    err_buf = ctypes.create_string_buffer(256)
    status = lib.inksight_atkinson_bw(
        in_buf,
        w,
        h,
        out_buf,
        err_buf,
        len(err_buf),
    )
    if status != 0:
        logger.warning(
            "native black/white Atkinson dithering failed: %s; using pure-Python fallback",
            err_buf.value.decode("utf-8", errors="replace"),
        )
        return _atkinson_bw_python(gray)
    return Image.frombytes("L", (w, h), out_buf.raw).convert("1", dither=Image.Dither.NONE)


def atkinson_palette(rgb: Image.Image, colors: int) -> Image.Image:
    lib = _load_lib()
    if colors not in (3, 4):
        raise ValueError("native palette Atkinson dithering supports only 3 or 4 colors")
    if lib is None:
        return _atkinson_palette_python(rgb, colors)
    src = rgb.convert("RGB")
    w, h = src.size
    in_buf = src.tobytes()
    out_buf = ctypes.create_string_buffer(w * h)
    err_buf = ctypes.create_string_buffer(256)
    status = lib.inksight_atkinson_palette(
        in_buf,
        w,
        h,
        int(colors),
        out_buf,
        err_buf,
        len(err_buf),
    )
    if status != 0:
        logger.warning(
            "native palette Atkinson dithering failed: %s; using pure-Python fallback",
            err_buf.value.decode("utf-8", errors="replace"),
        )
        return _atkinson_palette_python(rgb, colors)
    out = Image.frombytes("P", (w, h), out_buf.raw)
    out.putpalette(EINK_4COLOR_PALETTE + [0] * (768 - len(EINK_4COLOR_PALETTE)))
    return out
