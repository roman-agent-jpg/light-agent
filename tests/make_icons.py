"""Генерация иконок приложения (PWA) без внешних библиотек.

Рисует PNG вручную: zlib + struct. Ни PIL, ни ImageMagick не нужны —
важно, чтобы скрипт работал на любой машине.

Палитра — из оригинального Claude Code: терракотовый акцент #d77757
на тёплом тёмном фоне #141413.

Запуск:  python tests/make_icons.py
"""
import struct
import zlib
from pathlib import Path

OUT = Path("web/static/icons")
OUT.mkdir(parents=True, exist_ok=True)

# Цвета из палитры Claude Code
BG_TOP = (26, 26, 25)      # тёплый тёмный
BG_BOT = (16, 16, 15)
ACCENT = (215, 119, 87)   # терракотовый
ACCENT_HI = (224, 138, 107)
DOT = (250, 249, 245)     # почти белый


def png(width: int, height: int, pixels: bytes) -> bytes:
    """Собирает PNG из RGBA-пикселей."""
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    raw = bytearray()
    stride = width * 4
    for y in range(height):
        raw.append(0)  # фильтр строки: None
        raw.extend(pixels[y * stride:(y + 1) * stride])

    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
            + chunk(b"IEND", b""))


def draw(size: int, rounded: bool, maskable: bool = False) -> bytes:
    """Иконка в духе Claude: тёплый фон, терракотовое кольцо, белая точка."""
    px = bytearray(size * size * 4)
    r = size * 0.22 if rounded else 0  # радиус скругления
    cx = cy = size / 2

    # Размеры элементов. Для maskable всё уменьшено: Android обрезает края.
    scale = 0.78 if maskable else 1.0
    ring_outer = size * 0.30 * scale
    ring_inner = size * 0.20 * scale
    dot_r = size * (0.085 if maskable else 0.105) * scale

    for y in range(size):
        for x in range(size):
            i = (y * size + x) * 4
            dx, dy = x + 0.5 - cx, y + 0.5 - cy
            dist = (dx * dx + dy * dy) ** 0.5

            # Фон со скруглением
            if r:
                ox = max(abs(dx) - (size / 2 - r), 0)
                oy = max(abs(dy) - (size / 2 - r), 0)
                inside = (ox * ox + oy * oy) <= r * r
            else:
                inside = True

            if not inside:
                px[i:i + 4] = bytes((0, 0, 0, 0))
                continue

            # Вертикальный градиент фона - как в оригинале
            t = y / max(size - 1, 1)
            color = tuple(int(BG_TOP[k] + (BG_BOT[k] - BG_TOP[k]) * t)
                          for k in range(3))

            # Кольцо
            if ring_inner <= dist <= ring_outer:
                color = ACCENT
                # Верхняя дуга светлее - блик, как у значка Claude
                if dy < -size * 0.06:
                    color = ACCENT_HI

            # Центральная точка
            if dist <= dot_r:
                color = DOT if not maskable else ACCENT

            px[i:i + 4] = bytes((*color, 255))

    return png(size, size, bytes(px))


# 72 добавляем: он нужен манифесту как «одно из» уведомлений Android
SIZES = [16, 32, 48, 64, 72, 96, 128, 144, 152, 180, 192, 384, 512]

lines = []
for s in SIZES:
    # maskable нужны для Android: с запасом от обрезки
    data = draw(s, rounded=False if s <= 48 else True)
    (OUT / f"icon-{s}.png").write_bytes(data)
    lines.append(f"icon-{s}.png  {len(data)} Б")

# Иконка с маской для Android (весь квадрат залит, центр — 60%)
for s in (192, 512):
    data = draw(s, rounded=False, maskable=True)
    (OUT / f"maskable-{s}.png").write_bytes(data)
    lines.append(f"maskable-{s}.png  {len(data)} Б")

# Фавиконка для вкладки браузера
(OUT / "favicon.ico").write_bytes(draw(32, rounded=False))
lines.append("favicon.ico")

# Монохромная иконка для maskable в Android (системная)
lines.append("")
lines.append(f"всего файлов: {len(list(OUT.iterdir()))}")

with open("reports/icons.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("written reports/icons.txt")
print("\n".join(lines))