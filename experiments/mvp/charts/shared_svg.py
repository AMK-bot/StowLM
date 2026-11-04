"""Small, dependency-free SVG primitives for recorded-result charts."""

from html import escape


def attr(value):
    return escape(str(value), quote=True)


class Svg:
    """An opaque light canvas stays legible on either light or dark host pages."""

    def __init__(self, width, height, title, description):
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
            f'<title id="title">{escape(title)}</title>',
            f'<desc id="desc">{escape(description)}</desc>',
            '<style>text{font-family:Arial,Helvetica,sans-serif} '
            'line,rect,circle,path{vector-effect:non-scaling-stroke}</style>',
            f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
        ]

    def text(self, x, y, text, size=16, fill="#17212b", anchor="start", weight="normal"):
        self.parts.append(
            f'<text x="{x}" y="{y}" font-size="{size}" fill="{attr(fill)}" '
            f'text-anchor="{attr(anchor)}" font-weight="{attr(weight)}">{escape(str(text))}</text>'
        )

    def line(self, x1, y1, x2, y2, stroke="#9ca3af", width=1, dash=None):
        dashed = f' stroke-dasharray="{attr(dash)}"' if dash is not None else ""
        self.parts.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="{attr(stroke)}" stroke-width="{width}"{dashed}/>'
        )

    def rect(self, x, y, w, h, fill, rx=0, stroke="none"):
        self.parts.append(
            f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
            f'fill="{attr(fill)}" stroke="{attr(stroke)}"/>'
        )

    def circle(self, x, y, r, fill):
        self.parts.append(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{attr(fill)}"/>')

    def raw(self, markup):
        self.parts.append(markup)

    def finish(self):
        return "\n".join(self.parts + ["</svg>", ""])
