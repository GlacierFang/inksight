"""四色电子墨水屏插画归色（天工阁 / 躺平巴拉共用底层）。

背景
----
面板只有四个物理色：黑 / 白 / 黄 / 红，没有灰。传统做法是把每个像素硬归到
最近的一个颜色 —— 中间调全部丢失，画面只剩四块死板的色面（"生硬"）。

本模块的做法：**先分区，再在区内有序抖动**。

- 墨线在**原分辨率**上判定，缩放时"有墨即墨"（线宁可粗，不能消失）。
- 每个彩色区按**自己区内的**亮度范围做 3 级有序抖动：
  黄区 = 红→黄→白，红区 = 黑→红→黄。色阶按感知亮度排序（黑<红<黄<白）。
- 中性区（纸面 / 灰调）按亮度做黑白两级有序抖动：纸面留白，中间调出灰阶网点。
- 区与区之间**不混色**，所以不会出现跨色相的脏噪点。

输出永远是精确四色（EINK_4COLOR_PALETTE 的 12 个字节），与固件色码一一对应。
对已经是精确四色的平涂输入，本函数是幂等的（像素逐个相同）。
"""
from __future__ import annotations

from PIL import Image, ImageFilter

from .config import EINK_4COLOR_PALETTE

# 调色板索引，必须与 core/config.py 的 EINK_COLOR_NAME_MAP 一致
BLACK = 0
WHITE = 1
YELLOW = 2
RED = 3

# 8x8 Bayer 有序抖动阈值矩阵（0..63）
_BAYER8 = (
    (0, 32, 8, 40, 2, 34, 10, 42),
    (48, 16, 56, 24, 50, 18, 58, 26),
    (12, 44, 4, 36, 14, 46, 6, 38),
    (60, 28, 52, 20, 62, 30, 54, 22),
    (3, 35, 11, 43, 1, 33, 9, 41),
    (51, 19, 59, 27, 49, 17, 57, 25),
    (15, 47, 7, 39, 13, 45, 5, 37),
    (63, 31, 55, 23, 61, 29, 53, 21),
)

# 各区的色阶（按感知亮度从暗到亮）。区内的中间调靠有序抖动着色。
_YELLOW_RAMP = (RED, YELLOW, WHITE)
_RED_RAMP = (BLACK, RED, YELLOW)
_NEUTRAL_RAMP = (BLACK, WHITE)


def _shrink_any(mask: Image.Image, dw: int, dh: int) -> Image.Image:
    """有墨即墨地缩放一张掩膜：细线在缩放后仍然存在。

    e-ink 上宁可线粗一点，也不能让线消失。
    """
    small = mask.resize((dw, dh), Image.LANCZOS)
    return small.point(lambda p: 255 if p > 28 else 0, mode="L")


def _classify_native(
    src: Image.Image,
    *,
    ink_dark: int,
    ink_sat: int,
    color_sat: int,
    yellow_hue: tuple[int, int],
    red_hue: tuple[int, int],
) -> bytearray:
    """在原分辨率上把每个像素分到 黑 / 中性 / 黄 / 红。

    必须原分辨率判定：细墨线先缩放再分类会被抹平。
    """
    w, h = src.size
    hp, sp, vp = src.convert("HSV").split()
    h_data = hp.getdata()
    s_data = sp.getdata()
    v_data = vp.getdata()

    label = bytearray(w * h)
    y_lo, y_hi = yellow_hue
    r_lo, r_hi = red_hue

    for i in range(w * h):
        hsv_h = h_data[i]
        s = s_data[i]
        v = v_data[i]
        if s > color_sat and y_lo <= hsv_h <= y_hi:
            label[i] = YELLOW
        elif s > color_sat and (hsv_h <= r_lo or hsv_h >= r_hi):
            label[i] = RED
        elif v < ink_dark:
            # 暗部一律算墨线（含青绿等无法归色的色相），保住线稿
            label[i] = BLACK
        else:
            label[i] = WHITE  # 中性：纸面 / 灰调 / 无法归色的色相
    return label


def _region_range(
    label: bytearray, gray: list[int], target: int, lo_pct: float, hi_pct: float
) -> tuple[int, int] | None:
    """取某个彩色区自身的亮度范围（抗线稿的暗边污染）。"""
    vals = sorted(gray[i] for i, c in enumerate(label) if c == target)
    if not vals:
        return None
    lo = vals[int(len(vals) * lo_pct)]
    hi = vals[int(len(vals) * hi_pct)]
    if hi <= lo:
        return None
    return lo, hi


def _ordered_index(t: float, ramp: tuple[int, ...], tau: float) -> int:
    """把 0..1 的色调映射到一条色阶上，用 Bayer 阈值在相邻两色之间过渡。

    t 正好落在色阶中间那一档时输出该档颜色（所以平涂的色块不会长出网点）。
    """
    n = len(ramp)
    if n == 1:
        return ramp[0]
    pos = t * (n - 1)
    base = int(pos)
    if base >= n - 1:
        return ramp[n - 1]
    frac = pos - base
    return ramp[base + 1] if frac > tau else ramp[base]


def snap_illustration(
    src: Image.Image,
    width: int,
    height: int,
    *,
    ink_dark: int = 110,
    ink_sat: int = 45,
    color_sat: int = 50,
    yellow_hue: tuple[int, int] = (15, 48),
    red_hue: tuple[int, int] = (14, 240),
    paper_white: int = 230,
    ink_min: float = 0.0,
) -> Image.Image:
    """把源图归成精确四色，保留中间调层次。

    返回 P 模式图像，调色板为 EINK_4COLOR_PALETTE，索引 0..3 = 黑/白/黄/红。
    """
    src = src.convert("RGB")
    dw, dh = int(width), int(height)
    w, h = src.size

    label = _classify_native(
        src,
        ink_dark=ink_dark,
        ink_sat=ink_sat,
        color_sat=color_sat,
        yellow_hue=yellow_hue,
        red_hue=red_hue,
    )

    # 感知灰度（0..255），用于明暗层次
    gray_img = src.convert("L")
    gray = list(gray_img.getdata())

    # 各彩色区自己的亮度范围 —— 用来把区内明暗拉满（不依赖绝对色值）
    y_rng = _region_range(label, gray, YELLOW, 0.05, 0.95)
    r_rng = _region_range(label, gray, RED, 0.05, 0.95)

    # 原分辨率上算色调 t（0=最暗，1=最亮）
    tone = bytearray(w * h)
    for i in range(w * h):
        c = label[i]
        if c == BLACK:
            tone[i] = 255  # 墨线不参与色调计算
        elif c == YELLOW:
            if y_rng:
                lo, hi = y_rng
                tone[i] = int(max(0.0, min(1.0, (gray[i] - lo) / (hi - lo))) * 255)
            else:
                tone[i] = 128  # 平涂区：没有明暗层次，取色阶中点 = 纯色
        elif c == RED:
            if r_rng:
                lo, hi = r_rng
                tone[i] = int(max(0.0, min(1.0, (gray[i] - lo) / (hi - lo))) * 255)
            else:
                tone[i] = 128
        else:
            # 中性区：纸面留白，中间调出网点
            span = max(1, paper_white - ink_dark)
            t = (gray[i] - ink_dark) / span
            tone[i] = int(max(0.0, min(1.0, t)) * 255)

    # 掩膜：用 bytes.translate 做 C 级转换，避免逐像素 Python 开销
    label_bytes = bytes(label)
    masks = []
    for target in (BLACK, YELLOW, RED):
        table = bytes(255 if i == target else 0 for i in range(256))
        m = Image.frombytes("L", (w, h), label_bytes.translate(table))
        masks.append(_shrink_any(m, dw, dh))

    tone_img = Image.frombytes("L", (w, h), bytes(tone)).resize((dw, dh), Image.LANCZOS)
    gray_small = gray_img.resize((dw, dh), Image.LANCZOS)

    # 优先级合并：黑 > 金黄 > 朱红 > 中性
    mask_data = [m.getdata() for m in masks]
    tone_data = tone_img.getdata()
    index = bytearray(dw * dh)
    for y in range(dh):
        row = y * dw
        for x in range(dw):
            i = row + x
            if mask_data[0][i]:
                index[i] = BLACK
                continue
            tau = (_BAYER8[y & 7][x & 7] + 0.5) / 64.0
            t = tone_data[i] / 255.0
            if mask_data[1][i]:
                index[i] = _ordered_index(t, _YELLOW_RAMP, tau)
            elif mask_data[2][i]:
                index[i] = _ordered_index(t, _RED_RAMP, tau)
            else:
                index[i] = _ordered_index(t, _NEUTRAL_RAMP, tau)

    index = _boost_ink(index, gray_small, dw, dh, ink_min)

    out = Image.frombytes("P", (dw, dh), bytes(index))
    out.putpalette(EINK_4COLOR_PALETTE + [0] * (768 - len(EINK_4COLOR_PALETTE)))
    return out


def _boost_ink(
    index: bytearray, gray_small: Image.Image, dw: int, dh: int, target: float
) -> bytearray:
    """墨线太少就补墨：先把已有的墨线加粗，还不够就取最暗的一批像素补上。

    e-ink 上没有墨线的图会很淡。但补出来的墨必须是成片的，否则就是噪点 ——
    所以按亮度排序取整片最暗的像素，而不是随手点几个点。
    """
    if target <= 0:
        return index
    total = dw * dh
    need = int(target * total)
    black = sum(1 for v in index if v == BLACK)
    if black >= need:
        return index

    for _ in range(3):
        if black >= need:
            break
        mask = Image.new("L", (dw, dh), 255)
        mask.putdata([0 if v == BLACK else 255 for v in index])
        grown = mask.filter(ImageFilter.MaxFilter(3)).point(
            lambda p: 0 if p < 128 else 255, mode="L"
        )
        gp = grown.getdata()
        for i, v in enumerate(gp):
            if v == 0:
                index[i] = BLACK
        black = sum(1 for v in index if v == BLACK)

    if black < need:
        # 补在最暗的白色像素上，且不碰已有的彩色区（否则四色被吃成三色）
        gray = list(gray_small.getdata())
        cand = [(gray[i], i) for i, v in enumerate(index) if v == WHITE]
        cand.sort()
        for _, i in cand[: need - black]:
            index[i] = BLACK
    return index


def palette_counts(index_img: Image.Image) -> dict[str, float]:
    """统计四色占比，用于日志与验收。"""
    from collections import Counter

    names = ("黑", "白", "金黄", "朱红")
    cnt = Counter(index_img.getdata())
    total = index_img.size[0] * index_img.size[1]
    return {names[i]: cnt.get(i, 0) / total for i in range(4)}
