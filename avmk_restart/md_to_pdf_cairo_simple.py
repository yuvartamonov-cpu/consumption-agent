import sys
import re
import math
import cairo

A4_W = 595
A4_H = 842
MARGIN_L = 52
MARGIN_R = 52
MARGIN_T = 56
MARGIN_B = 56
CONTENT_W = A4_W - MARGIN_L - MARGIN_R
CONTENT_H = A4_H - MARGIN_T - MARGIN_B

FAMILY = "DejaVu Sans"

STYLES = {
    'title': dict(size=20, bold=True, italic=False, leading=28, before=0, after=10),
    'section': dict(size=17, bold=True, italic=False, leading=24, before=8, after=8),
    'subsection': dict(size=13, bold=True, italic=False, leading=18, before=4, after=4),
    'para': dict(size=10.5, bold=False, italic=False, leading=15, before=0, after=6),
    'note': dict(size=9.5, bold=False, italic=True, leading=14, before=0, after=6),
    'bullet': dict(size=10.5, bold=False, italic=False, leading=15, before=0, after=2),
}


def set_font(ctx, style):
    slant = cairo.FONT_SLANT_ITALIC if style['italic'] else cairo.FONT_SLANT_NORMAL
    weight = cairo.FONT_WEIGHT_BOLD if style['bold'] else cairo.FONT_WEIGHT_NORMAL
    ctx.select_font_face(FAMILY, slant, weight)
    ctx.set_font_size(style['size'])


def text_width(ctx, text):
    return ctx.text_extents(text).x_advance


def wrap_text(ctx, text, max_width):
    text = re.sub(r'\s+', ' ', text).strip()
    if not text:
        return ['']
    words = text.split(' ')
    lines = []
    cur = words[0]
    for w in words[1:]:
        candidate = cur + ' ' + w
        if text_width(ctx, candidate) <= max_width:
            cur = candidate
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


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
        txt = ' '.join(line[2:] if line.startswith('> ') else line for line in block)
        return ('note', txt)
    return ('para', re.sub(r'\*\*(.*?)\*\*', r'\1', joined.replace('  ', ' ')))


def draw_page_number(ctx, page_num):
    style = STYLES['note']
    set_font(ctx, style)
    text = str(page_num)
    w = text_width(ctx, text)
    ctx.move_to(A4_W - MARGIN_R - w, A4_H - 28)
    ctx.show_text(text)


def render_text_block(ctx, x, y, text, kind, width=CONTENT_W):
    style = STYLES[kind]
    set_font(ctx, style)
    lines = []
    for para_line in text.split('\n'):
        if para_line.strip() == '':
            lines.append('')
        else:
            lines.extend(wrap_text(ctx, para_line, width))
    used_lines = 0
    for line in lines:
        if line == '':
            y += style['leading'] * 0.6
        else:
            ctx.move_to(x, y)
            ctx.show_text(line)
            y += style['leading']
            used_lines += 1
    return y, max(1, used_lines) * style['leading']


def estimate_height(ctx, text, kind, width=CONTENT_W):
    style = STYLES[kind]
    set_font(ctx, style)
    lines = []
    for para_line in text.split('\n'):
        if para_line.strip() == '':
            lines.append('')
        else:
            lines.extend(wrap_text(ctx, para_line, width))
    count = 0
    for line in lines:
        count += 0.6 if line == '' else 1
    return style['before'] + math.ceil(count * style['leading']) + style['after']


def new_page(surface, ctx, page_num):
    if page_num[0] > 1:
        surface.show_page()
    ctx.set_source_rgb(0, 0, 0)


def main(inp, outp):
    text = open(inp, 'r', encoding='utf-8').read()
    blocks = [classify(b) for b in parse_blocks(text)]

    surface = cairo.PDFSurface(outp, A4_W, A4_H)
    ctx = cairo.Context(surface)
    page_num = [1]
    y = MARGIN_T

    for kind, content in blocks:
        if kind == 'section' and y > MARGIN_T + 20:
            draw_page_number(ctx, page_num[0])
            surface.show_page()
            page_num[0] += 1
            y = MARGIN_T
            ctx.set_source_rgb(0, 0, 0)

        if kind == 'bullets':
            for item in content:
                bullet_text = '• ' + item
                h = estimate_height(ctx, bullet_text, 'bullet', width=CONTENT_W - 8)
                if y + h > A4_H - MARGIN_B:
                    draw_page_number(ctx, page_num[0])
                    surface.show_page()
                    page_num[0] += 1
                    y = MARGIN_T
                    ctx.set_source_rgb(0, 0, 0)
                y += STYLES['bullet']['before']
                y, _ = render_text_block(ctx, MARGIN_L + 8, y, bullet_text, 'bullet', width=CONTENT_W - 8)
                y += STYLES['bullet']['after']
            y += 4
            continue

        h = estimate_height(ctx, content, kind)
        if y + h > A4_H - MARGIN_B:
            draw_page_number(ctx, page_num[0])
            surface.show_page()
            page_num[0] += 1
            y = MARGIN_T
            ctx.set_source_rgb(0, 0, 0)

        y += STYLES[kind]['before']
        y, _ = render_text_block(ctx, MARGIN_L, y, content, kind)
        y += STYLES[kind]['after']

    draw_page_number(ctx, page_num[0])
    surface.finish()


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print('usage: md_to_pdf_cairo_simple.py input.md output.pdf')
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
