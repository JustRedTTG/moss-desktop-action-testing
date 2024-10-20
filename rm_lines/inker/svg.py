"""Convert blocks to svg file.

Code originally from https://github.com/lschwetlick/maxio through
https://github.com/chemag/maxio .
"""

import logging
import string
from pathlib import Path

from typing import Iterable, Union

from .writing_tools import (
    Pen,
)
from ..scene_items import ParagraphStyle, Group, Line, Text
from ..scene_tree import SceneTree
from ..tagged_block_common import CrdtId
from ..text import TextDocument

SCREEN_WIDTH = 1404
SCREEN_HEIGHT = 1872
SCREEN_DPI = 226

SCALE = 100 / SCREEN_DPI

PAGE_WIDTH_PT = SCREEN_WIDTH * SCALE
PAGE_HEIGHT_PT = SCREEN_HEIGHT * SCALE
X_SHIFT = PAGE_WIDTH_PT // 2


def xx(screen_x):
    return screen_x * SCALE  # + X_SHIFT


def yy(screen_y):
    return screen_y * SCALE


TEXT_TOP_Y = -88
LINE_HEIGHTS = {
    # Tuned this line height using template grid -- it definitely seems to be
    # 71, rather than 70 or 72. Note however that it does interact a bit with
    # the initial text y-coordinate below.
    ParagraphStyle.BASIC: 100,
    ParagraphStyle.PLAIN: 71,
    ParagraphStyle.HEADING: 150,
    ParagraphStyle.BOLD: 70,
    ParagraphStyle.BULLET: 35,
    ParagraphStyle.BULLET2: 35,
    ParagraphStyle.CHECKBOX: 100,
    ParagraphStyle.CHECKBOX_CHECKED: 100,

    # There appears to be another format code (value 0) which is used when the
    # text starts far down the page, which case it has a negative offset (line
    # height) of about -20?
    #
    # Probably, actually, the line height should be added *after* the first
    # line, but there is still something a bit odd going on here.
}

# <html>
# <body>
# <div style="border: 1px solid grey; margin: 2em; float: left;">
# <svg xmlns="http://www.w3.org/2000/svg" height="$height" width="$width">
SVG_HEADER = string.Template("""<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" height="$height" width="$width" viewBox="$viewbox">
""")


def read_template_svg(template_path: Path) -> str:
    lines = template_path.read_text().splitlines()
    return "\n".join(lines[2:-1])


def tree_to_svg(tree: SceneTree, output, include_template=None):
    """Convert Tree to SVG."""

    # add svg header
    # output.write('<svg xmlns="http://www.w3.org/2000/svg">\n')
    output.write(SVG_HEADER.substitute(width=PAGE_WIDTH_PT,
                                       height=PAGE_HEIGHT_PT,
                                       viewbox=f"0 0 {PAGE_WIDTH_PT} {PAGE_HEIGHT_PT}") + "\n")

    if include_template is not None:
        template = read_template_svg(include_template)
        output.write(f'    <g id="template" style="display:inline" transform="scale({SCALE})">\n')
        output.write(template)
        output.write('    </g>\n')

    output.write(f'    <g id="p1" style="display:inline" transform="translate({X_SHIFT},0)">\n')
    # output.write('        <filter id="blurMe"><feGaussianBlur in="SourceGraphic" stdDeviation="10" /></filter>\n')

    # These special anchor IDs are for the top and bottom of the page.
    anchor_pos = {
        CrdtId(0, 281474976710654): 270,
        CrdtId(0, 281474976710655): 700,
    }
    if tree.root_text is not None:
        draw_text(tree.root_text, output, anchor_pos)

    draw_group(tree.root, output, anchor_pos)

    # # Overlay the page with a clickable rect to flip pages
    # output.write('\n')
    # output.write('        <!-- clickable rect to flip pages -->\n')
    # output.write(f'        <rect x="0" y="0" width="{svg_doc_info.width}" height="{svg_doc_info.height}" fill-opacity="0"/>\n')
    # Closing page group
    output.write('    </g>\n')
    # END notebook
    output.write('</svg>\n')


def draw_group(item: Group, output, anchor_pos):
    anchor_x = 0.0
    anchor_y = 0.0
    if item.anchor_id is not None:
        assert item.anchor_origin_x is not None
        anchor_x = item.anchor_origin_x.value
        if item.anchor_id.value in anchor_pos:
            anchor_y = anchor_pos[item.anchor_id.value]
    output.write(f'    <g id="{item.node_id}" transform="translate({xx(anchor_x)}, {yy(anchor_y)})">\n')
    for child_id in item.children:
        child = item.children[child_id]
        output.write(f'    <!-- child {child_id} -->\n')
        if isinstance(child, Group):
            draw_group(child, output, anchor_pos)
        elif isinstance(child, Line):
            draw_stroke(child, output)
    output.write(f'    </g>\n')


def draw_stroke(item: Line, output):
    # initiate the pen
    pen = Pen.create(item.tool.value, item.color.value, item.thickness_scale / 10)
    K = 5

    # BEGIN stroke
    output.write(
        f'        <!-- Stroke tool: {item.tool.name} color: {item.color.name} thickness_scale: {item.thickness_scale} -->\n')
    output.write('        <polyline ')
    output.write(
        f'style="fill:none;stroke:{pen.stroke_color};stroke-width:{pen.stroke_width / K};opacity:{pen.stroke_opacity}" ')
    output.write(f'stroke-linecap="{pen.stroke_linecap}" ')
    output.write(f'stroke-linejoin="{pen.stroke_linejoin}" ')
    output.write('points="')

    last_xpos = -1.
    last_ypos = -1.
    last_segment_width = segment_width = 0
    # Iterate through the point to form a polyline
    for point_id, point in enumerate(item.points):
        # align the original position
        xpos = point.x
        ypos = point.y
        if point_id % pen.segment_length == 0:
            segment_color = pen.get_segment_color(point.speed, point.direction, point.width, point.pressure,
                                                  last_segment_width)
            segment_width = pen.get_segment_width(point.speed, point.direction, point.width, point.pressure,
                                                  last_segment_width)
            segment_opacity = pen.get_segment_opacity(point.speed, point.direction, point.width, point.pressure,
                                                      last_segment_width)
            # print(segment_color, segment_width, segment_opacity, pen.stroke_linecap)
            # UPDATE stroke
            output.write('"/>\n')
            output.write('        <polyline ')
            output.write(
                f'style="fill:none; stroke:{segment_color} ;stroke-width:{segment_width / K:.3f};opacity:{segment_opacity}" ')
            output.write(f'stroke-linecap="{pen.stroke_linecap}" ')
            output.write(f'stroke-linejoin="{pen.stroke_linejoin}" ')
            output.write('points="')
            if last_xpos != -1.:
                # Join to previous segment
                output.write(a := f'{xx(last_xpos):.3f},{yy(last_ypos):.3f} ')
        # store the last position
        last_xpos = xpos
        last_ypos = ypos
        last_segment_width = segment_width

        # BEGIN and END polyline segment
        output.write(f'{xx(xpos):.3f},{yy(ypos):.3f} ')

    # END stroke
    output.write('" />\n')


def draw_text(text: Union[Text, TextDocument], output, anchor_pos):
    if isinstance(text, Text):
        text = TextDocument.from_scene_item(text)
    output.write('    <g class="root-text" style="display:inline">\n')

    # add some style to get readable text
    output.write('''
    <style>
        text.heading {
            font: 14pt serif;
        }
        text.bold {
            font: 8pt sans-serif bold;
        }
        text, text.plain {
            font: 7pt sans-serif;
        }
    </style>
    ''')

    y_offset = TEXT_TOP_Y
    pos_x = 0
    pos_y = 0
    for paragraph in text.contents:
        fmt = paragraph.style.value
        line = paragraph.contents[0].s if paragraph.contents else None
        if line:
            ids = paragraph.contents[0].i
        else:
            ids = []
        y_offset += LINE_HEIGHTS[fmt]

        pos_x
        pos_y += y_offset
        cls = fmt.name.lower()
        if line:
            output.write(f'        <!-- Text line char_id: {ids[0]} -->\n')
            output.write(f'        <text x="{xx(pos_x)}" y="{yy(pos_y)}" class="{cls}">{line.strip()}</text>\n')

        # Save y-coordinates of potential anchors
        for k in ids:
            anchor_pos[k] = pos_y

    output.write('    </g>\n')