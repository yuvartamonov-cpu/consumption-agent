import sys
import cairo

PAGE_W = 1320
PAGE_H = 1020
MARGIN = 0

PAGES = [
    ("rendered_design/design-01.png", "Обложка"),
    ("rendered_design/design-02.png", "Контекст и принципы"),
    ("rendered_design/design-03.png", "Обзор системы"),
    ("rendered_design/design-04.png", "Пять опор платформы"),
    ("rendered_design/design-06.png", "Видеоменеджмент"),
    ("rendered_design/design-08.png", "Тайм-машина"),
    ("rendered_design/design-09.png", "Архив операций"),
    ("rendered_design/design-10.png", "Телемедицина и ВКС"),
    ("rendered_design/design-13.png", "Инженерная интеграция"),
    ("rendered_design/design-14.png", "Чертежи и проектные материалы"),
    ("rendered_design/design-15.png", "Реализация"),
    ("rendered_design/design-16.png", "Готовая операционная"),
    ("rendered_design/design-17.png", "Сервис и поддержка"),
    ("rendered_design/design-18.png", "Финальный слайд"),
]


def draw_image(ctx, img_path, page_w, page_h):
    img = cairo.ImageSurface.create_from_png(img_path)
    iw = img.get_width()
    ih = img.get_height()
    scale = min(page_w / iw, page_h / ih)
    draw_w = iw * scale
    draw_h = ih * scale
    x = (page_w - draw_w) / 2
    y = (page_h - draw_h) / 2
    ctx.save()
    ctx.translate(x, y)
    ctx.scale(scale, scale)
    ctx.set_source_surface(img, 0, 0)
    ctx.paint()
    ctx.restore()


def main(out_path):
    surface = cairo.PDFSurface(out_path, PAGE_W, PAGE_H)
    ctx = cairo.Context(surface)

    for i, (img_path, _) in enumerate(PAGES):
        ctx.set_source_rgb(1, 1, 1)
        ctx.paint()
        draw_image(ctx, img_path, PAGE_W, PAGE_H)
        if i != len(PAGES) - 1:
            surface.show_page()

    surface.finish()


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print('usage: assemble_png_pdf.py output.pdf')
        sys.exit(1)
    main(sys.argv[1])
