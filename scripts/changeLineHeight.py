import os
import argparse
from fontTools.ttLib import TTFont
from fontTools.ttLib.ttFont import TTLibError

def adjust_ttf_line_height(ttf_path, output_path, scale=1.3):
    """修改单个 TTF 文件的行高参数"""
    try:
        font = TTFont(ttf_path)
        hhea = font["hhea"]
        os2 = font["OS/2"]

        # 原始参数（可能为负值，需注意符号）
        ascent = hhea.ascent
        descent = hhea.descent
        line_gap = hhea.lineGap

        # 调整参数（按比例扩大）
        hhea.ascent = int(ascent * scale)
        hhea.descent = int(descent * scale)
        hhea.lineGap = int(line_gap * scale)

        # 同步 OS/2 表参数（确保兼容性）
        os2.sTypoAscender = hhea.ascent
        os2.sTypoDescender = hhea.descent
        os2.sTypoLineGap = hhea.lineGap
        os2.usWinAscent = hhea.ascent
        os2.usWinDescent = hhea.descent * -1

        # 保存新字体文件
        font.save(output_path)
        print(f"成功: {os.path.basename(ttf_path)} -> {os.path.basename(output_path)}")
        
    except TTLibError as e:
        print(f"错误: {os.path.basename(ttf_path)} - {str(e)}")
    except Exception as e:
        print(f"未知错误: {os.path.basename(ttf_path)} - {str(e)}")

def batch_adjust_fonts(input_dir, output_dir, scale=1.3):
    """批量处理文件夹中的所有 TTF 字体"""
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # 遍历文件夹
    for filename in os.listdir(input_dir):
        if filename.lower().endswith(".ttf"):
            input_path = os.path.join(input_dir, filename)
            output_path = os.path.join(output_dir, f"{filename}")
            adjust_ttf_line_height(input_path, output_path, scale)

def main():
    parser = argparse.ArgumentParser(description="批量修改TTF字体的默认行距")
    parser.add_argument('input_folder', help="输入字体文件夹路径")
    parser.add_argument('-o', '--output', help='输出字体文件夹路径，默认覆盖原文件')
    parser.add_argument('-s', '--scale', type=float, default=1.2, help='行距缩放因子，默认为1.2')

    args = parser.parse_args()

    if not os.path.exists(args.input_folder):
        print(f"错误：文件夹 '{args.input_folder}' 不存在")
        return

    if not os.path.exists(args.input_folder):
        os.makedirs(args.output)

    # input_folder = "./ttf/"  # 替换为你的输入文件夹路径
    # output_folder = "ttf_linegap1p2"  # 替换为输出文件夹路径
    # batch_adjust_fonts(input_folder, output_folder, scale=1.2)
    batch_adjust_fonts(args.input_folder, args.output, args.scale)

if __name__ == "__main__":
    main()
