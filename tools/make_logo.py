"""生成市场图标 logo.png（512×512）。

纯几何绘制，不用 AI 生成：市场图标只有 48~192px 的展示尺寸，
矢量感的粗笔画比细节丰富的插画更容易看清，也没有版权与文字模糊问题。
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

S = 4  # 超采样倍数，缩回去时得到平滑边缘
W = 1024
BG = "#12161f"
AMBER = "#f0a020"


def main() -> None:
    img = Image.new("RGB", (W * S, W * S), BG)
    d = ImageDraw.Draw(img)

    def sc(v: float) -> int:
        return int(v * S)

    # 气泡主体
    d.rounded_rectangle(
        [sc(150), sc(205), sc(880), sc(700)], radius=sc(150), fill=AMBER
    )
    # 尾巴，朝左下
    d.polygon([(sc(290), sc(660)), (sc(285), sc(845)), (sc(455), sc(690))], fill=AMBER)

    # 准星：横竖两条，端点圆角
    # 半径必须远小于半宽，否则四条臂的圆头会在中心融成一朵"花瓣"，
    # 看起来像加号而不是准星——这是第一版踩的坑。
    cx, cy = sc(492), sc(432)
    # 外面一圈细环，把"医疗十字"拉回"准星"——这是准星最容易被认出的特征。
    ring = sc(150)
    d.ellipse(
        [cx - ring, cy - ring, cx + ring, cy + ring],
        outline="white",
        width=sc(17),
    )
    arm, half = sc(150), sc(34)
    d.rounded_rectangle(
        [cx - arm, cy - half, cx + arm, cy + half], radius=sc(16), fill="white"
    )
    d.rounded_rectangle(
        [cx - half, cy - arm, cx + half, cy + arm], radius=sc(16), fill="white"
    )

    # 右下小圆点，暗示"正在说话"
    d.ellipse([sc(742), sc(600), sc(806), sc(664)], fill="white")

    out = img.resize((512, 512), Image.LANCZOS)
    target = Path(__file__).resolve().parent.parent / "logo.png"
    out.save(target, optimize=True)
    print(f"{target}  {out.size[0]}x{out.size[1]}")


if __name__ == "__main__":
    main()
