import shutil
import subprocess
from pathlib import Path

from fontTools.ttLib import TTFont
from fontTools.pens.transformPen import TransformPen
from fontTools.pens.t2CharStringPen import T2CharStringPen


# ============================================================
# 配置
# ============================================================

INPUT_DIR = Path("./original_otf")
OTF_OUTPUT_DIR = Path("./otf")
TTF_OUTPUT_DIR = Path("./ttf")

SCALE_X = 1.0
SCALE_Y = 1.05

# 是否同步缩放字体的垂直 metrics。
# 对于“字形 Y 方向整体放大，同时保持 baseline=0”的场景，应保持开启。
SCALE_VERTICAL_METRICS = True

# 是否同步缩放 GPOS 中所有明确的 Y 坐标/位移：
#   Anchor.YCoordinate
#   ValueRecord.YPlacement
#   ValueRecord.YAdvance
# 这样组合音标、mark/base anchor、Y 方向定位等会跟随 glyph 一起缩放。
SCALE_GPOS_Y = True

# 是否缩放下划线/删除线等与 Y 方向有关的尺寸和位置。
SCALE_DECORATION_METRICS = True


OTF_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
TTF_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 通用辅助函数
# ============================================================


def _scale_int(value, factor):
    """按 OpenType 整数坐标规则缩放并四舍五入。"""
    return int(round(value * factor))


def _set_scaled_attr(obj, attr_name, factor, changes, label=None):
    """
    若对象具有指定整数属性，则按 factor 缩放，并记录变化。
    返回是否进行了修改。
    """
    if not hasattr(obj, attr_name):
        return False

    old_value = getattr(obj, attr_name)
    if old_value is None:
        return False

    new_value = _scale_int(old_value, factor)
    setattr(obj, attr_name, new_value)

    changes.append(
        f"    {(label or attr_name)}: {old_value} -> {new_value}"
    )
    return True


# ============================================================
# 垂直 metrics
# ============================================================


def scale_vertical_metrics(font: TTFont, factor: float):
    """
    将与字体垂直尺寸相关的 metrics 按 factor 缩放。

    关键原则：
      - glyph 仍然以 baseline (y=0) 为基准，不做整体 Y 平移；
      - ascent / descent / lineGap 与 glyph 一起缩放；
      - usWinAscent / usWinDescent 是正的无符号幅值，也直接乘 factor；
      - 横向 advance width 不改。
    """

    print("  Scale vertical metrics:")
    changes = []

    # --------------------------------------------------------
    # hhea
    # --------------------------------------------------------
    if "hhea" in font:
        hhea = font["hhea"]

        _set_scaled_attr(
            hhea, "ascent", factor, changes, "hhea.ascent"
        )
        _set_scaled_attr(
            hhea, "descent", factor, changes, "hhea.descent"
        )
        _set_scaled_attr(
            hhea, "lineGap", factor, changes, "hhea.lineGap"
        )

        # caretSlopeRise / caretSlopeRun 描述插入光标斜率。
        # 对纯 Y 缩放，rise 应同比缩放，run 保持不变。
        if hasattr(hhea, "caretSlopeRise"):
            _set_scaled_attr(
                hhea,
                "caretSlopeRise",
                factor,
                changes,
                "hhea.caretSlopeRise",
            )

    # --------------------------------------------------------
    # OS/2
    # --------------------------------------------------------
    if "OS/2" in font:
        os2 = font["OS/2"]

        for attr in (
            "sTypoAscender",
            "sTypoDescender",
            "sTypoLineGap",
            "usWinAscent",
            "usWinDescent",
        ):
            _set_scaled_attr(
                os2,
                attr,
                factor,
                changes,
                f"OS/2.{attr}",
            )

        # OS/2 version >= 2 通常包含 sxHeight / sCapHeight。
        for attr in (
            "sxHeight",
            "sCapHeight",
        ):
            _set_scaled_attr(
                os2,
                attr,
                factor,
                changes,
                f"OS/2.{attr}",
            )

        if SCALE_DECORATION_METRICS:
            for attr in (
                "yStrikeoutSize",
                "yStrikeoutPosition",
            ):
                _set_scaled_attr(
                    os2,
                    attr,
                    factor,
                    changes,
                    f"OS/2.{attr}",
                )

    # --------------------------------------------------------
    # post: underline
    # --------------------------------------------------------
    if SCALE_DECORATION_METRICS and "post" in font:
        post = font["post"]

        for attr in (
            "underlinePosition",
            "underlineThickness",
        ):
            _set_scaled_attr(
                post,
                attr,
                factor,
                changes,
                f"post.{attr}",
            )

    # --------------------------------------------------------
    # vhea: 若字体本身含 vertical metrics，则同步 Y 尺寸。
    # IBM Plex Mono 一般用于横排，通常没有 vhea/vmtx，
    # 这里作为通用兼容处理。
    # --------------------------------------------------------
    if "vhea" in font:
        vhea = font["vhea"]

        for attr in (
            "ascent",
            "descent",
            "lineGap",
        ):
            _set_scaled_attr(
                vhea,
                attr,
                factor,
                changes,
                f"vhea.{attr}",
            )

    if changes:
        for line in changes:
            print(line)
    else:
        print("    (no supported vertical metrics found)")


# ============================================================
# GPOS 中的 Y 坐标/位移
# ============================================================


def scale_gpos_y(font: TTFont, factor: float):
    """
    递归缩放 GPOS 中明确表示 Y 坐标/位移的字段：

      - Anchor.YCoordinate
      - ValueRecord.YPlacement
      - ValueRecord.YAdvance

    不修改：
      - XCoordinate / XPlacement / XAdvance
      - device/variation 表中的像素级修正
      - coverage / glyph class / lookup 结构本身

    这样做主要是为了避免 glyph 被纵向拉伸后，mark/base anchor
    仍停留在原来的 Y 坐标。
    """

    if "GPOS" not in font:
        print("  GPOS: not present")
        return

    print("  Scale GPOS Y coordinates/offsets:")

    root = font["GPOS"].table
    visited = set()
    change_count = 0

    # 只处理这些明确表示 Y 几何值的属性名。
    target_attrs = {
        "YCoordinate",
        "YPlacement",
        "YAdvance",
    }

    def walk(obj):
        nonlocal change_count

        if obj is None:
            return

        # 基础不可递归类型
        if isinstance(obj, (str, bytes, int, float, bool)):
            return

        obj_id = id(obj)
        if obj_id in visited:
            return
        visited.add(obj_id)

        if isinstance(obj, dict):
            for value in obj.values():
                walk(value)
            return

        if isinstance(obj, (list, tuple)):
            for value in obj:
                walk(value)
            return

        # fontTools OpenType table/value objects 通常都有 __dict__。
        data = getattr(obj, "__dict__", None)
        if not data:
            return

        for attr_name, value in list(data.items()):
            if attr_name in target_attrs and isinstance(value, int):
                new_value = _scale_int(value, factor)
                setattr(obj, attr_name, new_value)
                change_count += 1
            else:
                walk(value)

    walk(root)
    print(f"    scaled {change_count} GPOS Y values")


# ============================================================
# 第一步：修改 OTF 中的 CFF 字形
# ============================================================


def scale_cff_otf(input_path: Path, output_path: Path):
    """
    对普通 CFF OTF 做 Y 方向整体缩放：

        x' = x * SCALE_X
        y' = y * SCALE_Y

    baseline 保持 y=0，因为 dy=0。

    同时（默认开启）：
      - 同比缩放 hhea / OS/2 等 vertical metrics；
      - 同比缩放 GPOS 中明确的 Y anchor / Y placement；
      - 保持 hmtx advance width 不变。

    最终仍保存为 CFF OTF。
    """

    print(f"  Scale glyphs: {input_path.name}")

    font = TTFont(
        input_path,
        recalcBBoxes=True,
    )

    # --------------------------------------------------------
    # 检查字体格式
    # --------------------------------------------------------

    if "CFF2" in font:
        font.close()
        raise RuntimeError(
            f"{input_path.name} 是 CFF2 / Variable Font，"
            "此脚本只处理普通 CFF OTF。"
        )

    if "CFF " not in font:
        font.close()
        raise RuntimeError(
            f"{input_path.name} 不是 CFF OTF。"
        )

    # --------------------------------------------------------
    # 获取 CFF
    # --------------------------------------------------------

    cff = font["CFF "].cff
    top_dict = cff.topDictIndex[0]
    char_strings = top_dict.CharStrings

    # 原始 GlyphSet
    glyph_set = font.getGlyphSet()

    # --------------------------------------------------------
    # 不能边读取边替换：
    # 先生成全部新的 CharString，最后一次性替换。
    # --------------------------------------------------------

    new_char_strings = {}
    glyph_order = font.getGlyphOrder()

    for glyph_name in glyph_order:

        # 原来的 CharString
        old_cs, fd_select_index = char_strings.getItemAndSelector(
            glyph_name
        )

        # CID-keyed 字体可能每个 glyph 使用不同 Private Dict
        private = old_cs.private
        global_subrs = old_cs.globalSubrs

        # ----------------------------------------------------
        # 保持原 advance width
        # ----------------------------------------------------

        advance_width = font["hmtx"].metrics[glyph_name][0]

        default_width = getattr(
            private,
            "defaultWidthX",
            0,
        )

        nominal_width = getattr(
            private,
            "nominalWidthX",
            0,
        )

        # CFF Type2 CharString 中：
        #
        # width == defaultWidthX
        #     -> 不需要写 width
        # 否则：
        #     -> 写 advanceWidth - nominalWidthX
        #
        if advance_width == default_width:
            charstring_width = None
        else:
            charstring_width = (
                advance_width - nominal_width
            )

        # ----------------------------------------------------
        # 创建新的 CFF Type2 CharString
        # ----------------------------------------------------

        pen = T2CharStringPen(
            width=charstring_width,
            # 传入原 GlyphSet；极少数含 component/seac 的字体也可展开。
            glyphSet=glyph_set,
        )

        # ----------------------------------------------------
        # affine transform
        #
        # (xx, xy, yx, yy, dx, dy)
        #
        # x' = x * SCALE_X
        # y' = y * SCALE_Y
        #
        # 关键：dy = 0，baseline 不移动。
        # ----------------------------------------------------

        transform_pen = TransformPen(
            pen,
            (
                SCALE_X,
                0,
                0,
                SCALE_Y,
                0,
                0,
            ),
        )

        # 将原字形绘制到 transform pen
        glyph_set[glyph_name].draw(
            transform_pen
        )

        # 得到新的 Type2 CharString
        new_cs = pen.getCharString(
            private=private,
            globalSubrs=global_subrs,
        )

        # CID-keyed 字体保留 FDSelect
        if fd_select_index is not None:
            new_cs.fdSelectIndex = fd_select_index

        new_char_strings[glyph_name] = new_cs

    # --------------------------------------------------------
    # 全部生成完成之后再替换
    # --------------------------------------------------------

    for glyph_name, new_cs in new_char_strings.items():
        char_strings[glyph_name] = new_cs

    # --------------------------------------------------------
    # 同步 vertical metrics / GPOS
    # --------------------------------------------------------

    if SCALE_VERTICAL_METRICS:
        scale_vertical_metrics(font, SCALE_Y)

    if SCALE_GPOS_Y:
        scale_gpos_y(font, SCALE_Y)

    # --------------------------------------------------------
    # 保存
    #
    # recalcBBoxes=True 会在保存时重算相关 bbox。
    # --------------------------------------------------------

    font.save(output_path)
    font.close()

    print(f"  Scaled OTF: {output_path}")


# ============================================================
# 第二步：AFDKO OTF -> TTF
# ============================================================


def convert_otf_to_ttf(
    input_otf: Path,
    output_ttf: Path,
):
    """
    使用 AFDKO：

        afdko otf2ttf -o output.ttf input.otf

    如果系统没有 afdko 主命令，自动回退到旧的：

        otf2ttf -o output.ttf input.otf
    """

    afdko_exe = shutil.which("afdko")

    if afdko_exe:
        command = [
            afdko_exe,
            "otf2ttf",
            "-o",
            str(output_ttf),
            str(input_otf),
        ]
    else:
        otf2ttf_exe = shutil.which("otf2ttf")

        if not otf2ttf_exe:
            raise RuntimeError(
                "没有找到 AFDKO。\n\n"
                "请执行：\n"
                "    pip install afdko"
            )

        command = [
            otf2ttf_exe,
            "-o",
            str(output_ttf),
            str(input_otf),
        ]

    print(
        "  AFDKO:",
        " ".join(map(str, command)),
    )

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
    )

    if result.stdout:
        print(result.stdout)

    if result.returncode != 0:
        if result.stderr:
            print(result.stderr)

        raise RuntimeError(
            f"otf2ttf 转换失败：{input_otf.name}"
        )

    if not output_ttf.exists():
        raise RuntimeError(
            f"otf2ttf 没有生成目标文件："
            f"{output_ttf}"
        )

    print(f"  TTF: {output_ttf}")


# ============================================================
# 处理单个字体
# ============================================================


def process_font(input_path: Path):

    font_name = input_path.stem

    scaled_otf = (
        OTF_OUTPUT_DIR
        / f"{font_name}.otf"
    )

    output_ttf = (
        TTF_OUTPUT_DIR
        / f"{font_name}.ttf"
    )

    print()
    print("=" * 70)
    print(f"Processing: {input_path.name}")

    # 1. 原始 OTF -> 修改 CFF glyph + vertical metrics -> 新 OTF
    scale_cff_otf(
        input_path,
        scaled_otf,
    )

    # 2. 修改后的 OTF -> AFDKO -> TTF
    convert_otf_to_ttf(
        scaled_otf,
        output_ttf,
    )


# ============================================================
# main
# ============================================================


def main():

    if not INPUT_DIR.exists():
        raise RuntimeError(
            f"输入目录不存在：{INPUT_DIR}"
        )

    files = sorted(
        path
        for path in INPUT_DIR.iterdir()
        if (
            path.is_file()
            and path.suffix.lower() == ".otf"
        )
    )

    if not files:
        print(
            f"在 {INPUT_DIR} 中没有找到 OTF 字体。"
        )
        return

    success = 0
    failed = 0

    for input_path in files:
        try:
            process_font(input_path)
            success += 1

        except Exception as e:
            failed += 1

            print()
            print(
                f"[ERROR] {input_path.name}"
            )
            print(e)

    print()
    print("=" * 70)
    print(
        f"处理完成："
        f"{success} 成功，"
        f"{failed} 失败"
    )


if __name__ == "__main__":
    main()
