import sys
import re
import gi

gi.require_foreign('cairo')
import cairo

gi.require_version('Pango', '1.0')
gi.require_version('PangoCairo', '1.0')
from gi.repository import Pango, PangoCairo

A4_W = 595
A4_H = 842
MARGIN_L = 52
MARGIN_R = 52
MARGIN_T = 56
MARGIN_B = 56
CONTENT_W = A4_W - MARGIN_L - MARGIN_R
CONTENT_H = A4_H - MARGIN_T - MARGIN_B

TITLE = "DejaVu Sans Bold 20"
H1 = "DejaVu Sans Bold 18"
H2 = "DejaVu Sans Bold 14"
BODY = "DejaVu Sans 10.5"
SMALL = "DejaVu Sans Oblique 9.5"
BULLET = "DejaVu Sans 10.5"


def make_layout(ctx, text, font, width=CONTENT_W, markup=False):
    layout = PangoCairo.create_layout(ctx)
    layout.set_width(int(width * Pango.SCALE))
    layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    layout.set_font_description(Pango.FontDescription(font))
    if markup:
        layout.set_markup(text, -1)
    else:
        layout.set_text(text, -1)
    return layout


def layout_height(layout):
    return layout.get_pixel_size()[1]


def draw_layout(ctx, layout, x, y):
    ctx.move_to(x, y)
    PangoCairo.show_layout(ctx, layout)


def draw_page_number(ctx, page_num):
    layout = make_layout(ctx, str(page_num), SMALL, width=40)
    w, h = layout.get_pixel_size()
    draw_layout(ctx, layout, A4_W - MARGIN_R - w, A4_H - MARGIN_B + 18)


def parse_blocks(text):
    lines = text.splitlines()
    blocks = []
    cur = []
    for line in lines:
        if line.strip() == '':
            if cur:
                blocks.append(cur)
                cur = []
        else:
            cur.append(line.rstrip())
    if cur:
        blocks.append(cur)
    return blocks


def classify(block):
    first = block[0]
    joined = '\n'.join(block)
    if first.startswith('# '):
        return ('title', first[2:].strip())
    if first.startswith('## '):
        return ('section', first[3:].strip())
    if first.startswith('### '):
        return ('subsection', first[4:].strip())
    if all(line.startswith('- ') for line in block):
        return ('bullets', [line[2:].strip() for line in block])
    if first.startswith('> '):
        txt = '\n'.join(line[2:] if line.startswith('> ') else line for line in block)
        return ('note', txt)
    return ('para', joined)


def ensure_space(ctx, surface, y, needed, page_num_ref):
    if y + needed > MARGIN_T + CONTENT_H:
        draw_page_number(ctx, page_num_ref[0])
        surface.show_page()
        page_num_ref[0] += 1
        return MARGIN_T
    return y


def main(inp, outp):
    text = open(inp, 'r', encoding='utf-8').read()
    blocks = [classify(b) for b in parse_blocks(text)]

    surface = cairo.PDFSurface(outp, A4_W, A4_H)
    ctx = cairo.Context(surface)
    ctx.set_source_rgb(0, 0, 0)
    page_num = [1]
    y = MARGIN_T

    for kind, content in blocks:
        if kind == 'title':
            layout = make_layout(ctx, content, TITLE)
            h = layout_height(layout)
            y = ensure_space(ctx, surface, y, h + 20, page_num)
            draw_layout(ctx, layout, MARGIN_L, y)
            y += h + 18
        elif kind == 'section':
            if y > MARGIN_T + 10:
                draw_page_number(ctx, page_num[0])
                surface.show_page()
                page_num[0] += 1
                y = MARGIN_T
            layout = make_layout(ctx, content, H1)
            h = layout_height(layout)
            draw_layout(ctx, layout, MARGIN_L, y)
            y += h + 14
        elif kind == 'subsection':
            layout = make_layout(ctx, content, H2)
            h = layout_height(layout)
            y = ensure_space(ctx, surface, y, h + 10, page_num)
            draw_layout(ctx, layout, MARGIN_L, y)
            y += h + 8
        elif kind == 'bullets':
            for item in content:
                markup = f"• {item}"
                layout = make_layout(ctx, markup, BULLET)
                h = layout_height(layout)
                y = ensure_space(ctx, surface, y, h + 4, page_num)
                draw_layout(ctx, layout, MARGIN_L + 8, y)
                y += h + 4
            y += 6
        elif kind == 'note':
            layout = make_layout(ctx, content, SMALL)
            h = layout_height(layout)
            y = ensure_space(ctx, surface, y, h + 8, page_num)
            draw_layout(ctx, layout, MARGIN_L, y)
            y += h + 10
        else:
            para = re.sub(r'\*\*(.*?)\*\*', r'\1', content)
            para = para.replace('  \n', '\n')
            layout = make_layout(ctx, para, BODY)
            h = layout_height(layout)
            y = ensure_space(ctx, surface, y, h + 8, page_num)
            draw_layout(ctx, layout, MARGIN_L, y)
            y += h + 10

    draw_page_number(ctx, page_num[0])
    surface.finish()


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print('usage: md_to_pdf_pango.py input.md output.pdf')
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
