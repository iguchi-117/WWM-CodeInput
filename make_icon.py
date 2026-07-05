#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WWM コード高速入力ツール - アイコン生成
PIL (Pillow) があれば使用、なければ tkinter.PhotoImage で代替作成。
"""
import sys
from pathlib import Path

ICON_ICO = Path(__file__).parent / "app.ico"


def make_with_pil():
    """Pillow を使って高品質アイコン生成"""
    from PIL import Image, ImageDraw, ImageFont

    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    images = []
    for size in sizes:
        img = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # 角丸背景 (青系グラデ)
        for y in range(size[1]):
            ratio = y / max(1, size[1] - 1)
            r = int(43 + (90 - 43) * ratio)
            g = int(58 + (140 - 58) * ratio)
            b = int(85 + (200 - 85) * ratio)
            draw.line([(0, y), (size[0], y)], fill=(r, g, b, 255))
        # クリップボードアイコン風
        cx, cy = size[0] // 2, size[1] // 2
        # クリップ部分
        clip_w = max(8, size[0] // 3)
        clip_h = max(4, size[1] // 8)
        draw.rectangle(
            [cx - clip_w // 2, cy - size[1] // 3,
             cx + clip_w // 2, cy - size[1] // 3 + clip_h],
            fill=(255, 255, 255, 230),
        )
        # 本体
        body_top = cy - size[1] // 3 + clip_h // 2
        body_bot = cy + size[1] // 3
        body_left = cx - size[0] // 3
        body_right = cx + size[0] // 3
        draw.rounded_rectangle(
            [body_left, body_top, body_right, body_bot],
            radius=max(2, size[0] // 12),
            fill=(255, 255, 255, 240),
            outline=(43, 58, 85, 255),
            width=max(1, size[0] // 32),
        )
        # "W" テキスト
        try:
            font_size = max(8, size[0] // 3)
            font = ImageFont.truetype("arialbd.ttf", font_size)
        except OSError:
            font = ImageFont.load_default()
        text = "W"
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        draw.text(
            (cx - text_w // 2 - bbox[0], cy - text_h // 2 - bbox[1] + clip_h // 4),
            text,
            fill=(43, 58, 85, 255),
            font=font,
        )
        images.append(img)

    # マルチサイズ ICO として保存
    images[0].save(
        ICON_ICO,
        format="ICO",
        sizes=[(img.width, img.height) for img in images],
        append_images=images[1:],
    )
    print(f"[OK] Pillow でアイコン生成: {ICON_ICO} ({len(sizes)} サイズ)")


def make_with_tkinter():
    """Pillow が無い場合: tkinter.PhotoImage で PNG を作って ICO に変換"""
    import tkinter as tk

    root = tk.Tk()
    root.withdraw()

    # 256x256 の PhotoImage を作成
    size = 256
    img = tk.PhotoImage(width=size, height=size)

    # 単色塗り（青系）
    img.put("#2b3a55", to=(0, 0, size, size))
    # 白い枠
    img.put("#ffffff", to=(20, 20, size - 20, size - 20))
    img.put("#2b3a55", to=(40, 40, size - 40, size - 40))
    # クリップボード本体
    img.put("#ffffff", to=(80, 100, size - 80, size - 60))
    # "W" の代わりの装飾
    img.put("#2b3a55", to=(100, 130, size - 100, size - 90))

    # PNG として一旦保存
    png_path = ICON_ICO.with_suffix(".png")
    img.write(str(png_path), format="png")

    # ICO 変換は PIL が必要なので、PNG のまま使う選択肢もある
    # exe ビルドには ICO が必要なので、ここでは PNG だけ作る
    print(f"[OK] tkinter で PNG 生成: {png_path}")
    print("    ※ .ico 変換には Pillow が必要です。build_exe.py はデフォルトアイコンで続行します。")
    root.destroy()


if __name__ == "__main__":
    try:
        make_with_pil()
    except ImportError:
        print("Pillow が無いので tkinter で代替生成します...")
        make_with_tkinter()
