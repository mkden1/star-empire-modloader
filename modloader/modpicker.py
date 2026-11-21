"""modpicker.py - the window for choosing mods.

    Choose Mods.bat                      the window, to change the mods you use
    the launch file                      opens it by itself the first time, before the game starts (``--play``)

On the left every mod in mods_available/ with a tick box, grouped (Features, Speed fixes ...); on the right the selected
mod's README and its screenshots. Ticking a mod adds the mods it needs; Recommended ticks the usual set; Apply makes mods/
match. It is drawn with pygame, which the package already carries: no server, no browser, nothing to install. Exit code 0
means carry on (applied, or skipped), 2 means the player closed it to cancel (the launch file then does not start the game).

The logic (what is ticked, what applying does) is modpicker_model.py; this file is only the window.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_LIB = os.path.join(_ROOT, "runtime", "lib")                 # the package's own pygame
if os.path.isdir(_LIB):
    try:
        os.add_dll_directory(_LIB)
    except (AttributeError, OSError):
        pass
    if _LIB not in sys.path:
        sys.path.append(_LIB)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import re  # noqa: E402
import time  # noqa: E402

import pygame  # noqa: E402

import modpicker_model as model  # noqa: E402

BG, PANEL, BORDER = (13, 17, 27), (19, 25, 39), (46, 58, 84)
TEXT, DIM, ACCENT = (218, 226, 240), (137, 152, 176), (110, 178, 255)
GOOD, WARN = (98, 201, 141), (240, 192, 92)
ROW_HOVER, ROW_SELECTED = (27, 37, 58), (36, 53, 86)
BUTTON, BUTTON_HOVER = (38, 52, 80), (54, 74, 112)
PRIMARY, PRIMARY_HOVER = (38, 112, 198), (54, 134, 224)
CODE_COLOR = (178, 214, 255)

UI_FONTS = "segoeui,calibri,arial,helveticaneue,helvetica,dejavusans,freesans"
MONO_FONTS = "consolas,couriernew,dejavusansmono,freemono"


class Fonts:
    """One font per size class ("body", "small", "title", "head") and style ("normal", "bold", "code")."""

    def __init__(self, scale: float):
        def make(names, size, bold=False):
            return pygame.font.SysFont(names, max(9, round(size * scale)), bold=bold)
        self.by = {}
        for size_class, size in (("body", 16), ("small", 14)):
            self.by[size_class, "normal"] = make(UI_FONTS, size)
            self.by[size_class, "bold"] = make(UI_FONTS, size, True)
            self.by[size_class, "code"] = make(MONO_FONTS, size - 1)
        self.title = make(UI_FONTS, 26, True)
        self.head = make(UI_FONTS, 18, True)
        self.row_title = self.by["body", "normal"]
        self.row_summary = self.by["small", "normal"]


_INLINE = re.compile(r"(\*\*.+?\*\*|`.+?`)")


def inline_runs(text: str):
    """``[(text, style)]`` of a line of README text: **bold** and `code` become styles, the rest is normal."""
    runs = []
    for part in _INLINE.split(text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**") and len(part) > 4:
            runs.append((part[2:-2], "bold"))
        elif part.startswith("`") and part.endswith("`") and len(part) > 2:
            runs.append((part[1:-1], "code"))
        else:
            runs.append((part, "normal"))
    return runs


def plain(text: str) -> str:
    """The text without its **bold** and `code` marks (for the one-line summaries in the list)."""
    return "".join(part for part, _style in inline_runs(text))


def wrap_runs(runs, fonts: Fonts, size_class: str, width: int):
    """Break styled text into lines no wider than ``width``: ``[[(text, style), ...], ...]``. A word longer than a line
    is left to overflow rather than cut."""
    atoms = []
    for text, style in runs:
        for piece in re.split(r"(\s+)", text):
            if piece:
                atoms.append((" " if piece.isspace() else piece, style))
    lines, line, x = [], [], 0
    for text, style in atoms:
        w = fonts.by[size_class, style].size(text)[0]
        if text == " ":
            if line:
                line.append((text, style))
                x += w
            continue
        if x + w > width and line:
            while line and line[-1][0] == " ":
                line.pop()
            lines.append(line)
            line, x = [], 0
        line.append((text, style))
        x += w
    while line and line[-1][0] == " ":
        line.pop()
    if line:
        lines.append(line)
    return lines


def fit_text(font, text: str, width: int) -> str:
    if font.size(text)[0] <= width:
        return text
    while text and font.size(text + "...")[0] > width:
        text = text[:-1]
    return text.rstrip() + "..."


def layout_detail(entry, width: int, fonts: Fonts, scale: float):
    """Lay out a mod's README for a panel ``width`` pixels wide: ``(ops, height)``. ``ops`` are what to draw:
    ``("text", x, y, string, font, color)``, ``("image", x, y, surface)``, ``("rule", y)``."""
    ops, y = [], 0
    gap = round(8 * scale)

    def paragraph(runs, size_class, x, color, wrap_width, dim=False, bullet_x=None):
        """Lay out wrapped runs. Every run of a line sits on one baseline (a code font and a bold one have different
        ascents). ``bullet_x`` puts a bullet in front of the first line."""
        nonlocal y
        for number, line in enumerate(wrap_runs(runs, fonts, size_class, wrap_width)):
            used = [fonts.by[size_class, style] for _text, style in line] or [fonts.by[size_class, "normal"]]
            ascent, height = max(f.get_ascent() for f in used), max(f.get_linesize() for f in used)
            if number == 0 and bullet_x is not None:
                plain = fonts.by[size_class, "normal"]
                ops.append(("text", bullet_x, y + ascent - plain.get_ascent(), "•", plain, DIM))
            cx = x
            for text, style in line:
                font = fonts.by[size_class, style]
                ops.append(("text", cx, y + ascent - font.get_ascent(), text, font,
                            CODE_COLOR if style == "code" and not dim else color))
                cx += font.size(text)[0]
            y += height
        y += round(3 * scale)

    title_words = entry.title.split(" ")                                            # the title, in the big font
    row, rows = "", []
    for word in title_words:
        trial = (row + " " + word).strip()
        if fonts.title.size(trial)[0] > width and row:
            rows.append(row)
            row = word
        else:
            row = trial
    rows.append(row)
    for row in rows:
        ops.append(("text", 0, y, row, fonts.title, TEXT))
        y += fonts.title.get_linesize()
    paragraph([("Name on the in-game Mods page: ", "normal"), (entry.name, "code")], "small", 0, DIM, width)
    y += gap
    if entry.summary:
        paragraph(inline_runs(entry.summary), "body", 0, DIM, width)
        y += gap
    for path in entry.screenshots:
        try:
            image = pygame.image.load(str(path))
        except (pygame.error, OSError):
            paragraph([(f"({path.name} could not be loaded)", "normal")], "small", 0, DIM, width)
            continue
        iw, ih = image.get_size()
        factor = min(1.0, width / iw, round(380 * scale) / ih)
        if factor < 1.0:
            image = pygame.transform.smoothscale(image, (max(1, int(iw * factor)), max(1, int(ih * factor))))
        ops.append(("image", 0, y, image))
        y += image.get_height() + round(3 * scale)
        caption = entry.captions.get(path.name) or path.stem.replace("-", " ").replace("_", " ")
        paragraph([(caption, "normal")], "small", 0, DIM, width)
        y += gap // 2
    for heading, body in entry.sections:
        technical = heading.lower() == "under the hood"
        y += gap
        ops.append(("text", 0, y, heading, fonts.head, DIM if technical else ACCENT))
        y += fonts.head.get_linesize() + round(2 * scale)
        size_class = "small" if technical else "body"
        for line in body:
            if not line.strip():
                continue
            if line.startswith("- "):
                bullet = round(16 * scale)
                paragraph(inline_runs(line[2:]), size_class, bullet, DIM if technical else TEXT, width - bullet, technical,
                          bullet_x=round(2 * scale))
            else:
                paragraph(inline_runs(line), size_class, 0, DIM if technical else TEXT, width, technical)
    return ops, y + gap


def draw_ops(surface, ops) -> None:
    for op in ops:
        if op[0] == "text":
            _, x, y, text, font, color = op
            surface.blit(font.render(text, True, color), (x, y))
        elif op[0] == "image":
            surface.blit(op[3], (op[1], op[2]))


class Button:
    def __init__(self, label, kind="normal"):
        self.label, self.kind, self.rect = label, kind, pygame.Rect(0, 0, 0, 0)


class Picker:
    """The window. ``available`` / ``installed`` default to mods_available/ and mods/ beside the loader folder."""

    def __init__(self, root=None, play=False, scale=1.0, size=(1120, 720), available=None, installed=None):
        self.root = root or _ROOT
        self.available = available or os.path.join(self.root, "mods_available")
        self.installed = installed or os.path.join(self.root, "mods")
        self.play, self.scale, self.size = play, float(scale), size
        self.catalog = model.load_catalog(self.available)
        self.selection = model.initial_selection(self.catalog, self.installed)
        self.fonts = None
        self.groups = self.catalog.grouped()
        order = [e.name for _k, _l, members in self.groups for e in members]
        self.selected = order[0] if order else None
        self.order = order
        self.list_scroll = self.detail_scroll = 0
        self.mouse = (-1, -1)
        self.result = None                      # "apply", "skip" or "cancel"
        self.message = ""
        self._detail_cache = {}
        self.primary = Button("Apply and play" if play else "Apply", "primary")
        self.secondary = Button("Play without mods" if play else "Cancel")
        self.recommended_button, self.none_button = Button("Recommended"), Button("None")
        self.buttons = [self.primary, self.secondary, self.recommended_button, self.none_button]
        self.report = None

    # -- geometry ------------------------------------------------------------------------------------------------
    def px(self, n: float) -> int:
        return round(n * self.scale)

    def layout(self, width: int, height: int) -> dict:
        pad, header, footer = self.px(14), self.px(78), self.px(64)
        list_w = max(self.px(320), int(width * 0.36))
        body_h = max(self.px(120), height - header - footer)
        list_rect = pygame.Rect(pad, header, list_w, body_h)
        detail_rect = pygame.Rect(list_rect.right + pad, header, max(self.px(200), width - list_rect.right - 2 * pad), body_h)
        return {"pad": pad, "header": header, "footer": pygame.Rect(0, height - footer, width, footer),
                "list": list_rect, "detail": detail_rect, "status": pygame.Rect(detail_rect.x, detail_rect.y, detail_rect.w, self.px(46)),
                "scroll": pygame.Rect(detail_rect.x, detail_rect.y + self.px(46) + self.px(6), detail_rect.w,
                                      detail_rect.h - self.px(46) - self.px(6))}

    def place_buttons(self, lay: dict) -> None:
        foot, h, gap = lay["footer"], self.px(38), self.px(10)
        y = foot.y + (foot.h - h) // 2
        font = self.fonts.by["body", "bold"]

        def width(button):
            return font.size(button.label)[0] + self.px(36)
        x = foot.right - lay["pad"]
        for button in (self.primary, self.secondary):
            x -= width(button)
            button.rect = pygame.Rect(x, y, width(button), h)
            x -= gap
        x = lay["pad"]
        for button in (self.recommended_button, self.none_button):
            button.rect = pygame.Rect(x, y, width(button), h)
            x += width(button) + gap

    def rows(self, lay: dict):
        """``[(kind, key, rect, entry)]`` for every row of the list, with the scroll applied (rects can be off screen)."""
        out, y = [], lay["list"].y - self.list_scroll
        for key, label, members in self.groups:
            out.append(("header", key, pygame.Rect(lay["list"].x, y, lay["list"].w, self.px(34)), label))
            y += self.px(34)
            for entry in members:
                out.append(("mod", entry.name, pygame.Rect(lay["list"].x, y, lay["list"].w, self.px(54)), entry))
                y += self.px(54)
        return out

    def list_height(self) -> int:
        return sum(self.px(34) + self.px(54) * len(members) for _k, _l, members in self.groups)

    def clamp(self, lay: dict) -> None:
        self.list_scroll = max(0, min(self.list_scroll, max(0, self.list_height() - lay["list"].h)))
        _ops, height = self.detail(lay)
        self.detail_scroll = max(0, min(self.detail_scroll, max(0, height - lay["scroll"].h)))

    def detail(self, lay: dict):
        entry = self.catalog.by_name.get(self.selected)
        if entry is None:
            return [], 0
        width = lay["scroll"].w - self.px(40)                 # room for the padding and for the scrollbar
        key = (entry.name, width)
        if key not in self._detail_cache:
            self._detail_cache[key] = layout_detail(entry, width, self.fonts, self.scale)
        return self._detail_cache[key]

    def checkbox_rect(self, row_rect) -> "pygame.Rect":
        size = self.px(22)
        return pygame.Rect(row_rect.x + self.px(14), row_rect.y + (row_rect.h - size) // 2, size, size)

    # -- events --------------------------------------------------------------------------------------------------
    def handle_event(self, event, lay: dict) -> None:
        if event.type == pygame.QUIT:
            self.result = "cancel"
        elif event.type == pygame.MOUSEMOTION:
            self.mouse = event.pos
        elif event.type == pygame.MOUSEWHEEL:
            step = -event.y * self.px(54)
            if lay["list"].collidepoint(self.mouse):
                self.list_scroll += step
            elif lay["detail"].collidepoint(self.mouse):
                self.detail_scroll += step
            self.clamp(lay)
        elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
            self.mouse = event.pos
            self.click(event.pos, lay)
        elif event.type == pygame.KEYDOWN:
            self.key(event, lay)

    def click(self, pos, lay: dict) -> None:
        if self.primary.rect.collidepoint(pos):
            self.result = "apply"
        elif self.secondary.rect.collidepoint(pos):
            self.result = "skip" if self.play else "cancel"
        elif self.recommended_button.rect.collidepoint(pos):
            self.selection.recommended()
        elif self.none_button.rect.collidepoint(pos):
            self.selection.none()
        elif lay["list"].collidepoint(pos):
            for kind, key, rect, _entry in self.rows(lay):
                if kind == "mod" and rect.collidepoint(pos):
                    if self.checkbox_rect(rect).inflate(self.px(14), self.px(14)).collidepoint(pos):
                        self.selection.toggle(key)
                    if key != self.selected:
                        self.selected, self.detail_scroll = key, 0
                    break

    def key(self, event, lay: dict) -> None:
        if event.key == pygame.K_ESCAPE:
            self.result = "cancel"
        elif event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.result = "apply"
        elif event.key == pygame.K_SPACE and self.selected:
            self.selection.toggle(self.selected)
        elif event.key in (pygame.K_UP, pygame.K_DOWN, pygame.K_HOME, pygame.K_END) and self.order:
            index = self.order.index(self.selected) if self.selected in self.order else 0
            index = {pygame.K_UP: index - 1, pygame.K_DOWN: index + 1, pygame.K_HOME: 0, pygame.K_END: len(self.order) - 1}[event.key]
            self.selected, self.detail_scroll = self.order[max(0, min(index, len(self.order) - 1))], 0
            for kind, key, rect, _entry in self.rows(lay):          # scroll the list so the row is in view
                if kind == "mod" and key == self.selected:
                    if rect.top < lay["list"].top:
                        self.list_scroll -= lay["list"].top - rect.top
                    elif rect.bottom > lay["list"].bottom:
                        self.list_scroll += rect.bottom - lay["list"].bottom
            self.clamp(lay)
        elif event.key in (pygame.K_PAGEDOWN, pygame.K_PAGEUP):
            self.detail_scroll += (1 if event.key == pygame.K_PAGEDOWN else -1) * lay["scroll"].h * 0.85
            self.clamp(lay)

    # -- drawing -------------------------------------------------------------------------------------------------
    def draw(self, surface) -> dict:
        if self.fonts is None:
            self.fonts = Fonts(self.scale)
        width, height = surface.get_size()
        lay = self.layout(width, height)
        self.place_buttons(lay)
        self.clamp(lay)
        surface.fill(BG)
        self.draw_header(surface, lay)
        self.draw_list(surface, lay)
        self.draw_detail(surface, lay)
        self.draw_footer(surface, lay)
        if self.message:
            self.draw_message(surface)
        return lay

    def draw_header(self, surface, lay) -> None:
        pad = lay["pad"]
        surface.blit(self.fonts.title.render("Choose your mods", True, TEXT), (pad, self.px(10)))
        hint = ("Tick the mods you want, then press Apply and play. Change them later with Choose Mods.bat." if self.play else
                "Tick the mods you want, then press Apply. They are used the next time you start the game.")
        surface.blit(self.fonts.by["small", "normal"].render(hint, True, DIM), (pad, self.px(10) + self.fonts.title.get_linesize() + self.px(2)))

    def draw_list(self, surface, lay) -> None:
        rect = lay["list"]
        pygame.draw.rect(surface, PANEL, rect, border_radius=self.px(6))
        previous = surface.get_clip()
        surface.set_clip(rect)
        hover = self.mouse
        if not self.groups:
            text = self.fonts.by["body", "normal"].render("No mods found.", True, DIM)
            surface.blit(text, (rect.x + self.px(14), rect.y + self.px(14)))
        for kind, key, row, extra in self.rows(lay):
            if row.bottom < rect.top or row.top > rect.bottom:
                continue
            if kind == "header":
                label = self.fonts.by["small", "bold"].render(extra.upper(), True, ACCENT)
                surface.blit(label, (row.x + self.px(14), row.y + self.px(12)))
                help_text = model.CATEGORY_HELP.get(key, "")
                if help_text:
                    tip = self.fonts.by["small", "normal"].render(help_text, True, DIM)
                    surface.blit(tip, (row.x + self.px(24) + label.get_width(), row.y + self.px(12)))
                continue
            if key == self.selected:
                pygame.draw.rect(surface, ROW_SELECTED, row.inflate(-self.px(8), -self.px(4)), border_radius=self.px(5))
            elif row.collidepoint(hover) and rect.collidepoint(hover):
                pygame.draw.rect(surface, ROW_HOVER, row.inflate(-self.px(8), -self.px(4)), border_radius=self.px(5))
            self.draw_checkbox(surface, self.checkbox_rect(row), self.selection.state(key))
            text_x = self.checkbox_rect(row).right + self.px(14)
            room = row.right - text_x - self.px(14)
            surface.blit(self.fonts.row_title.render(fit_text(self.fonts.row_title, extra.title, room), True, TEXT), (text_x, row.y + self.px(8)))
            surface.blit(self.fonts.row_summary.render(fit_text(self.fonts.row_summary, plain(extra.summary), room), True, DIM), (text_x, row.y + self.px(30)))
        surface.set_clip(previous)
        self.draw_scrollbar(surface, rect, self.list_scroll, self.list_height())

    def draw_checkbox(self, surface, rect, state: str) -> None:
        radius = self.px(4)
        if state == "off":
            pygame.draw.rect(surface, (120, 138, 170), rect, width=max(1, self.px(2)), border_radius=radius)
            return
        pygame.draw.rect(surface, PRIMARY if state == "on" else (62, 90, 140), rect, border_radius=radius)
        a, b, c = (rect.x + rect.w * 0.22, rect.y + rect.h * 0.52), (rect.x + rect.w * 0.43, rect.y + rect.h * 0.74), (rect.x + rect.w * 0.80, rect.y + rect.h * 0.28)
        color = (255, 255, 255) if state == "on" else (196, 212, 238)
        pygame.draw.lines(surface, color, False, [a, b, c], max(2, self.px(3)))

    def draw_scrollbar(self, surface, rect, scroll: float, total: float) -> None:
        if total <= rect.h:
            return
        track = pygame.Rect(rect.right - self.px(8), rect.y + self.px(4), self.px(5), rect.h - self.px(8))
        thumb_h = max(self.px(30), int(track.h * rect.h / total))
        thumb_y = track.y + int((track.h - thumb_h) * scroll / max(1, total - rect.h))
        pygame.draw.rect(surface, (30, 40, 62), track, border_radius=self.px(3))
        pygame.draw.rect(surface, (84, 104, 146), pygame.Rect(track.x, thumb_y, track.w, thumb_h), border_radius=self.px(3))

    def draw_detail(self, surface, lay) -> None:
        panel = lay["detail"]
        pygame.draw.rect(surface, PANEL, panel, border_radius=self.px(6))
        entry = self.catalog.by_name.get(self.selected)
        if entry is None:
            text = ("There are no mods to choose from yet. Clone the repository's mods_available branch into a folder called "
                    "mods_available next to this program.")
            for i, line in enumerate(wrap_runs([(text, "normal")], self.fonts, "body", panel.w - self.px(28))):
                x = panel.x + self.px(14)
                for part, style in line:
                    surface.blit(self.fonts.by["body", style].render(part, True, TEXT), (x, panel.y + self.px(14) + i * self.px(22)))
                    x += self.fonts.by["body", style].size(part)[0]
            return
        self.draw_status(surface, lay["status"], entry)
        view = lay["scroll"]
        ops, height = self.detail(lay)
        content = pygame.Surface((view.w - self.px(40), max(1, height)), pygame.SRCALPHA)
        draw_ops(content, ops)
        previous = surface.get_clip()
        surface.set_clip(view)
        surface.blit(content, (view.x + self.px(14), view.y - int(self.detail_scroll)))
        surface.set_clip(previous)
        self.draw_scrollbar(surface, view, self.detail_scroll, height)

    def draw_status(self, surface, rect, entry) -> None:
        state = self.selection.state(entry.name)
        label = dict(model.CATEGORIES).get(entry.category, "")
        small = self.fonts.by["small", "bold"]
        needed_by = self.selection.needed_by(entry.name)
        if state == "on":
            text, color = "In your selection", GOOD
            if needed_by:
                text += " (also needed by " + ", ".join(needed_by) + ")"
        elif state == "auto":
            text, color = "Added automatically: needed by " + ", ".join(needed_by or ["another mod"]), WARN
        else:
            adds = self.selection.adds(entry.name)
            text, color = "Not in your selection", DIM
            if adds:
                text += " (ticking it also adds " + ", ".join(adds) + ")"
        x, right = rect.x + self.px(14), rect.right - self.px(28)          # the scrollbar below sits at the right edge
        if entry.recommended:
            tag = self.fonts.by["small", "normal"].render("Recommended", True, GOOD)
            right -= tag.get_width() + self.px(12)
            surface.blit(tag, (right + self.px(12), rect.y + self.px(8)))
        if label:
            chip = small.render(label.upper(), True, ACCENT)
            surface.blit(chip, (x, rect.y + self.px(8)))
            x += chip.get_width() + self.px(12)
        surface.blit(small.render(fit_text(small, text, right - x), True, color), (x, rect.y + self.px(8)))

    def draw_footer(self, surface, lay) -> None:
        foot = lay["footer"]
        pygame.draw.line(surface, BORDER, (0, foot.y), (foot.w, foot.y))
        font = self.fonts.by["body", "bold"]
        for button in self.buttons:
            hover = button.rect.collidepoint(self.mouse)
            color = (PRIMARY_HOVER if hover else PRIMARY) if button.kind == "primary" else (BUTTON_HOVER if hover else BUTTON)
            pygame.draw.rect(surface, color, button.rect, border_radius=self.px(6))
            label = font.render(button.label, True, (255, 255, 255))
            surface.blit(label, label.get_rect(center=button.rect.center))
        closure = self.selection.closure()
        extra = len(closure) - len(self.selection.chosen)
        text = f"{len(self.selection.chosen)} chosen" + (f", {extra} more added because they are needed" if extra else "")
        own = len(model.installed_names(self.installed) - set(self.catalog.by_name))
        if own:
            text += f"; {own} of your own in mods/ left alone"
        left = self.none_button.rect.right + self.px(24)
        room = self.secondary.rect.left - left - self.px(16)
        if room > self.px(80):
            small = self.fonts.by["small", "normal"]
            surface.blit(small.render(fit_text(small, text, room), True, DIM), (left, foot.y + (foot.h - small.get_height()) // 2))

    def draw_message(self, surface) -> None:
        width, height = surface.get_size()
        shade = pygame.Surface((width, height), pygame.SRCALPHA)
        shade.fill((8, 10, 16, 190))
        surface.blit(shade, (0, 0))
        text = self.fonts.head.render(self.message, True, TEXT)
        box = text.get_rect(center=(width // 2, height // 2)).inflate(self.px(60), self.px(40))
        pygame.draw.rect(surface, PANEL, box, border_radius=self.px(8))
        pygame.draw.rect(surface, BORDER, box, width=1, border_radius=self.px(8))
        surface.blit(text, text.get_rect(center=box.center))

    # -- running -------------------------------------------------------------------------------------------------
    def finish(self) -> int:
        """Do what the player chose (after the window loop); returns the exit code."""
        if self.result == "apply":
            self.report = model.apply_selection(self.selection, self.available, self.installed)
            return model.EXIT_CONTINUE
        if self.result == "skip":
            model.mark_chosen(self.installed)
            return model.EXIT_CONTINUE
        return model.EXIT_CANCELLED

    def run(self) -> int:
        pygame.init()
        try:
            screen_w, screen_h = pygame.display.get_desktop_sizes()[0]
        except (pygame.error, IndexError):
            screen_w, screen_h = 1920, 1080
        if screen_w < 400 or screen_h < 300:                    # no real display to ask (or a dummy one)
            screen_w, screen_h = 1920, 1080
        size = (min(self.px(self.size[0]), int(screen_w * 0.94)), min(self.px(self.size[1]), int(screen_h * 0.9)))
        screen = pygame.display.set_mode(size, pygame.RESIZABLE)
        pygame.display.set_caption("Star Empire mods: choose your mods")
        lay = self.draw(screen)
        pygame.display.flip()
        while self.result is None:
            events = [pygame.event.wait(250)] + pygame.event.get()
            for event in events:
                if event.type == pygame.WINDOWCLOSE:
                    self.result = "cancel"
                self.handle_event(event, lay)
            lay = self.draw(screen)
            pygame.display.flip()
        code = self.finish()
        if self.report is not None and not self.play:
            self.message = "Saved. The mods are used the next time you start the game."
            self.draw(screen)
            pygame.display.flip()
            time.sleep(1.4)
        pygame.quit()
        return code


def detect_scale() -> float:
    """The Windows display scale (1.0 at 100%, 1.5 at 150% ...), so the window is not tiny on a high-resolution screen."""
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
        return max(1.0, min(3.0, ctypes.windll.user32.GetDpiForSystem() / 96.0))
    except Exception:
        return 1.0


def main(argv) -> int:
    root, play, scale = _ROOT, False, None
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "--play":
            play = True
        elif arg == "--root" and args:
            root = args.pop(0)
        elif arg == "--scale" and args:
            scale = float(args.pop(0))
    try:
        return Picker(root, play=play, scale=scale or detect_scale()).run()
    except Exception:
        import traceback
        text = traceback.format_exc()
        try:
            with open(os.path.join(root, "picker_error.txt"), "w", encoding="utf-8") as handle:
                handle.write(text)
        except OSError:
            pass
        try:                                              # no console under pythonw: say it where it cannot be missed
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, "The mod picker could not open:\n\n" + text[-900:] +
                                             "\n\nYou can still copy mod folders into the mods folder by hand.",
                                             "Star Empire mods", 0x10)
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
