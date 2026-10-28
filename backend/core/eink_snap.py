"""四色电子墨水屏插画归色。

背景
----
面板只有四个物理色：黑 / 白 / 黄 / 红，没有灰。传统做法是把每个像素硬归到
最近的一个颜色 —— 中间调全部丢失，画面只剩四块死板的色面（"生硬"）。

**当前在用的路径**（2026-10-28 冰河大人拍板，天工阁与躺平巴拉统一）：

- :func:`prepare_direct_art` —— **天工阁 + 躺平巴拉**都用。**不做预四色化**，
  只保证下采样不把细节平均掉，颜色归类交给渲染器的 Atkinson 抖动
  （标准路径，输出精确四色）。

  为什么不再预四色化：天工阁原图 512×768 要压到 156×234（3.3 倍），
  而原图的墨线只有 1 像素宽、亮度中位数 168。直接 LANCZOS 缩放是邻域平均，
  压完灰度 <110 的像素只剩 3 个 —— 墨线在**下采样**那一步就没了，四色化
  再怎么修都找不回来。屏上调色板又没有灰（白 255 / 黄 175 / 红 43 / 黑 0），
  被稀释成中灰的线亮度正好落在黄上，抖动器把它判成黄，成了黄色虚点。

  躺平巴拉是同样的病：预四色化把大色块打成有序网点，原图的毛色细节
  反而被抹平了；改走直接抖动后，毛发、轮廓、眼睛都更实（实测三张场景
  逐像素对比一致变好）。

- :func:`snap_illustration` —— **历史路径，当前没有调用者**，保留备查。
  它先把图分区（金黄区 / 朱红区 / 中性区），再在区内做有序抖动，
  输出精确四色 PNG。2026-10-28 之前天工阁与躺平巴拉都走它。
  已被 :func:`prepare_direct_art` 取代，理由见上。

两条路径的共同点：

- 细节不能因为缩放而消失（一条用"有墨即墨"的掩膜，一条用 min-pool）。
- 屏上永远是那四个物理色（EINK_4COLOR_PALETTE），与固件色码一一对应。
"""
from __future__ import annotations

from PIL import Image, ImageChops, ImageFilter, ImageOps

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


# --------------------------------------------------------------------------
# 天工阁专用：直接上屏（不做预四色化）
# --------------------------------------------------------------------------
#
# 这条路径替换掉了原来的 snap_line_art（预四色化 + 网点过渡）。原因是实测：
#
# 生图原图 512×768 要压到屏上的 156×234（3.3 倍），而原图的墨线只有
# 1 像素宽、亮度中位数 168。直接 LANCZOS 缩放是邻域平均，1 像素宽的深线
# 被摊薄到亮度接近纸白 —— 压完灰度 <110 的像素只剩 3 个，墨线在
# **下采样**那一步就没了，四色化再怎么修都找不回来。
#
# 更糟的是屏上调色板没有灰（白 255 / 黄 175 / 红 43 / 黑 0）：被稀释成
# 中灰的线亮度正好落在黄上，抖动器把它判成黄，于是成了黄色虚点。
#
# 所以这里只做一件事：下采样时别把细线平均掉（_minpool_resize）。
# 颜色怎么归，交给渲染器的 Atkinson 四色抖动 —— 那是渲染器的标准路径，
# 输出正是精确四色（EINK_4COLOR_PALETTE，索引 0-3），与固件色码一致。
#
# 附带的好处：省掉一层自己的量化，天工阁和其它模式走同一条路。


def _content_bbox(img: Image.Image) -> tuple[int, int, int, int] | None:
    """找出画面的有效内容范围（去掉白边）。

    判定用"不是白纸"：亮度低于 240 的算内容；饱和度高于 60 的也算内容
    （淡金色的高光接近纸白，但它是画面的一部分，不能被裁掉）。
    """
    lum = img.convert("L").point(lambda p: 255 if p < 240 else 0)
    sat = img.convert("HSV").split()[1].point(lambda p: 255 if p > 60 else 0)
    return ImageChops.lighter(lum, sat).getbbox()


# 选择性拉伸的饱和度区间：低于 _STRETCH_S_LO 完全拉伸，高于 _STRETCH_S_HI 完全不拉伸。
#
# 实测（生图原图 512x768 的饱和度分布）：
#   金黄区 S: min=91  p05=112  p50=183
#   朱红区 S: min=91  p05=152  p50=175
#   无彩色区（纸白/墨线）S: p50=3  p95=22  max=90
#
# 彩色与无彩色在 S=90/91 处干净分开，所以取 30~90 的过渡带：
# 纸白和墨线（都很低饱和）被拉伸，金黄朱红原样保留。
_STRETCH_S_LO = 30
_STRETCH_S_HI = 90

# 纸面判定的两个阈值：明度够高 且 饱和度够低 = 纸。
#
# 实测生图原图 512x768：
#   纸面（米白） S=29..84  V=245..254
#   金黄区       S>=91       V 各异
#   朱红区       S>=91       V 各异
#   墨线         S<40        V=38..170
#
# 为什么必须把纸面置为**精确白**：模型有时把纸画成米白（比如 252,240,168），
# 这个颜色在屏上四色里没有对应。Atkinson 误差扩散处理这种颜色时，
# 误差会累积成一片黄网纹（实测米黄袍身 -> 满屏黄色网纹）。
# 直接把它写成 (255,255,255)，误差为 0，网纹就没了。
#
# 只提 V 不提 S 没用：米黄提亮后饱和度还在，仍然不是调色板色。
_PAPER_V = 200
_PAPER_S = 120


def _stretch_value(img: Image.Image) -> Image.Image:
    """只拉伸无彩色区（纸白 / 墨线）的明度，彩色区（金黄 / 朱红）不动。

    为什么需要这一步：渲染器的选色规则是 RGB 欧氏距离取最近调色板色
    （见 core/native/eink_dither.cpp 的 nearest_index）。屏上四色里黄是
    (232,176,0)，明度约 175，和纸白一样亮。于是一条中灰的线（明度 168
    上下）落在黄的区间，Atkinson 误差扩散一累积，线就被抖成黄色虚点。

    实测最近邻归属：灰度 90 -> 黑，110~150 -> 黄，168 -> 白。
    所以要让线真正变成黑，源图的线必须暗到 100 以下。

    为什么不整体拉 V：整体拉会把金色内部的深浅笔画一起拉开，深金被判成黑，
    金色块变成麻点。只拉无彩色区，线变黑而金色保持实心
    （实测金黄 6.0%、朱红 2.7%，线清晰）。

    这是标准的前处理（对比度归一），不改颜色语义：色相不变、饱和度不变，
    只是把无彩色区的明度分布拉满。
    """
    h, s, v = img.convert("HSV").split()
    v_stretched = ImageOps.autocontrast(v, cutoff=1)
    # 每像素混合系数：S 越低越接近 1（完全用拉伸后的明度）
    lut = []
    span = _STRETCH_S_HI - _STRETCH_S_LO
    for i in range(256):
        t = (_STRETCH_S_HI - i) / float(span)
        lut.append(int(255 * max(0.0, min(1.0, t))))
    blend = s.point(lut)
    v_mixed = Image.composite(v_stretched, v, blend)
    return Image.merge("HSV", (h, s, v_mixed)).convert("RGB")


def _whiten_paper(img: Image.Image) -> Image.Image:
    """把纸面区直接置为精确白 (255,255,255)。

    纸面判据：明度 >= _PAPER_V 且 饱和度 <= _PAPER_S（色相不限）。

    为什么不是"提亮"而是"置白"：屏上四色没有米白、米黄这种中间色。
    模型画出的纸面如果是 (252,240,168) 这种米白，它在四色里没有归属，
    Atkinson 误差扩散处理不了 —— 误差会累积成一片黄网纹。
    写成精确白后误差为 0，纸面就是干净的白纸。

    彩色区（金黄 S>=91、朱红 S>=91）饱和度高于 _PAPER_S，不会被误伤。
    """
    h, s, v = img.convert("HSV").split()
    low_s = s.point(lambda p: 255 if p <= _PAPER_S else 0)
    high_v = v.point(lambda p: 255 if p >= _PAPER_V else 0)
    paper = ImageChops.multiply(low_s, high_v)
    white = Image.new("RGB", img.size, (255, 255, 255))
    return Image.composite(white, img, paper)


def _minpool_resize(img: Image.Image, tw: int, th: int) -> Image.Image:
    """缩小时逐级先取邻域最暗像素（MinFilter），再 LANCZOS。

    MinFilter 取邻域里最暗的那个像素，等于"这条线在这个范围内存在"；
    LANCZOS 取平均，等于"线被纸白稀释掉了"。线稿要前者。

    这是线稿的标准处理，不改变任何颜色语义，只是别让缩放把细结构抹平。
    """
    w, h = img.size
    im = img
    while w > tw * 2:
        nw = max(tw, w // 2)
        factor = w / nw
        k = int(round(factor)) | 1
        if k >= 3:
            im = im.filter(ImageFilter.MinFilter(k))
        im = im.resize((nw, max(1, int(h * nw / w))), Image.LANCZOS)
        w, h = im.size
    return im.resize((tw, th), Image.LANCZOS)


def prepare_direct_art(src: Image.Image, width: int, height: int) -> Image.Image:
    """把生图原图准备成可直接上屏的 RGB 图（不做四色归色）。

    天工阁与躺平巴拉共用（2026-10-28 起统一）。

    四步：

    1. 裁掉白边，但把裁剪框撑成目标长宽比 —— 否则硬拉成目标尺寸会变形
       （旧的 snap_line_art 就把 782×875 的裁剪图硬拉成 160×200，横向压扁 25%）。
    2. 拉 V 通道明度（见 :func:`_stretch_value`），把中灰的线压到能被
       判成黑的亮度区间。不这一步，线会被抖成黄色虚点。
    3. 纸面置为精确白（见 :func:`_whiten_paper`）。模型有时把纸画成米白，
       这种颜色在屏上四色里没有归属，Atkinson 会把它抖成一片黄网纹。
    4. 用 min-pool 下采样，保住细墨线（见 :func:`_minpool_resize`）。
    5. 输出 RGB，尺寸与屏上显示尺寸 1:1，四色归类交给渲染器。

    参数
    ----
    src:
        生图原图（RGB）。
    width, height:
        屏上显示尺寸，必须与模式 JSON 里 image 块的 width/height 一致，
        这样渲染器 fit=contain 时是 1:1，不会再缩一次（也就不会二次稀释细线）。
    """
    img = src.convert("RGB")
    tw, th = int(width), int(height)
    target = tw / th

    img = _stretch_value(img)
    img = _whiten_paper(img)

    bbox = _content_bbox(img)
    if bbox:
        x0, y0, x1, y1 = bbox
        bw, bh = max(1, x1 - x0), max(1, y1 - y0)
        if bw / bh > target:
            # 内容太宽：保宽，上下扩到目标比例
            grow = (bw / target - bh) / 2
            y0 = max(0, int(y0 - grow))
            y1 = min(img.height, int(y1 + grow))
        else:
            # 内容太高：保高，左右扩到目标比例
            grow = (bh * target - bw) / 2
            x0 = max(0, int(x0 - grow))
            x1 = min(img.width, int(x1 + grow))
        img = img.crop((x0, y0, x1, y1))

    return _minpool_resize(img, tw, th)


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
