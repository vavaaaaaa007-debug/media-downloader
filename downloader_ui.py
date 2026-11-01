# -*- coding: utf-8 -*-
"""
媒体下载器的新界面（浅紫配色，圆角胶囊）。

只使用 Python 自带的 tkinter，不增加任何依赖；圆角、胶囊和卡片都是用 Canvas 画的。
下载逻辑全部在 drive_image_downloader.py 中，这里只负责显示和交互。
如果这个界面无法启动，主程序会自动退回经典界面。
"""
import os
import sys
import threading
import time
from urllib.parse import urlsplit

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    from tkinter import font as tkfont
except ImportError:  # 没有 tkinter 时由主程序处理
    tk = ttk = filedialog = messagebox = tkfont = None

# ------------------------------------------------------------------ 配色
BG = "#FDFBFF"
HEADER_BG = "#F4EEFE"
FIELD = "#F7F3FE"
BORDER = "#E3D6FA"
BORDER_STRONG = "#BFA6F4"
TEXT = "#2A1A5E"
TEXT2 = "#6B5A9E"
TEXT3 = "#7A6BA8"
CHIP = "#EDE4FD"
CHIP_TEXT = "#4A2CA0"
BADGE = "#E1D4FB"
BADGE_TEXT = "#2E1A66"
ACCENT = "#5B2BC4"
LOGO = "#753DE6"
PRIMARY = "#8654E0"
PRIMARY_HOVER = "#7A47D8"
PRIMARY_OFF = "#A98EEB"
YELLOW = "#FDE05C"
YELLOW_TEXT = "#4A3A00"
OK = "#2F7D4F"
ERR = "#B3261E"
RUN = "#753DE6"

SHORTCUT = "⌘V" if sys.platform == "darwin" else "Ctrl+V"
PAD_CHOICES = ["不补零", "2位（01）", "3位（001）"]
PAD_VALUES = {"不补零": 0, "2位（01）": 2, "3位（001）": 3}


# ------------------------------------------------------------------ 纯计算（可单独测试）
def rounded_points(x1, y1, x2, y2, r):
    """圆角矩形的折点，配合 create_polygon(smooth=True) 使用。"""
    r = max(0, min(r, (x2 - x1) / 2.0, (y2 - y1) / 2.0))
    return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
            x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]


def _cubic(p0, p1, p2, p3, steps=14):
    pts = []
    for i in range(1, steps + 1):
        t = i / float(steps)
        u = 1 - t
        pts.append((u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t ** 3 * p3[0],
                    u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t ** 3 * p3[1]))
    return pts


def cloud_outline(scale=1.0, ox=0.0, oy=0.0):
    """logo 里的云朵线条（100x100 坐标系缩放后的折线，中间留出箭头的缺口）。"""
    pts = [(38, 68), (30, 68)]
    cur = (30, 68)
    for c1, c2, end in (((19, 68), (13, 59), (14, 50)), ((15, 42), (22, 37), (30, 37)),
                        ((32, 26), (41, 20), (50, 20)), ((60, 20), (67, 27), (69, 37)),
                        ((78, 37), (86, 43), (86, 52)), ((86, 61), (80, 68), (70, 68))):
        pts.extend(_cubic(cur, c1, c2, end))
        cur = end
    pts.append((62, 68))
    flat = []
    for x, y in pts:
        flat.extend((x * scale + ox, y * scale + oy))
    return flat


def logo_arrow(scale=1.0, ox=0.0, oy=0.0):
    pts = [(44, 54), (56, 54), (56, 70), (63, 70), (50, 84), (37, 70), (44, 70)]
    flat = []
    for x, y in pts:
        flat.extend((x * scale + ox, y * scale + oy))
    return flat


CARD_HEADER = 26
ROW_HEIGHT = 19
CARD_PAD = 9


def card_height(rows):
    return CARD_PAD + CARD_HEADER + rows * ROW_HEIGHT + CARD_PAD


def layout_cards(row_counts, width, columns=2, gap=8, margin=10):
    """每张卡片的 (x, y, 宽, 高)，同一排的卡片等高；返回 (布局列表, 总高度)。"""
    if width < 430:
        columns = 1
    inner = max(120, width - 2 * margin)
    card_w = (inner - gap * (columns - 1)) / float(columns)
    out, y = [], margin
    for start in range(0, len(row_counts), columns):
        chunk = row_counts[start:start + columns]
        h = max(card_height(c) for c in chunk)
        for k in range(len(chunk)):
            out.append((margin + k * (card_w + gap), y, card_w, h))
        y += h + gap
    total = (y - gap + margin) if row_counts else 2 * margin
    return out, total


def display_link(kind, fid, link):
    """卡片里显示的链接缩写（完整链接仍保存在后台）。"""
    if kind == "drive" and fid:
        return "…/d/%s…" % fid[:14]
    if kind == "facebook":
        parts = urlsplit(link)
        tail = [p for p in parts.path.split("/") if p]
        return "%s/…/%s" % (parts.netloc.replace("www.", ""), tail[-1][:14] if tail else "")
    return link if len(link) <= 40 else link[:39] + "…"


def fit_text(font, text, max_width):
    """超出宽度时用省略号截断。"""
    if max_width <= 0:
        return ""
    if font.measure(text) <= max_width:
        return text
    while len(text) > 1 and font.measure(text + "…") > max_width:
        text = text[:-1]
    return text + "…"


def summarize(plan):
    """(系列数, 单文件数, 可下载文件数)。"""
    sizes = {}
    for e in plan["entries"]:
        sizes[e["group"]] = e["size"]
    valid_groups = {e["group"] for e in plan["items"]}
    series = sum(1 for g in valid_groups if sizes[g] > 1)
    singles = sum(1 for g in valid_groups if sizes[g] == 1)
    return series, singles, len(plan["items"])


# ------------------------------------------------------------------ 小图标
def _icon_download(c, cx, cy, color):
    c.create_line(cx, cy - 4, cx, cy + 2, fill=color, width=1.6, capstyle="round")
    c.create_line(cx - 3, cy - 1, cx, cy + 2, cx + 3, cy - 1, fill=color, width=1.6,
                  capstyle="round", joinstyle="round")
    c.create_line(cx - 4, cy + 4.5, cx + 4, cy + 4.5, fill=color, width=1.6, capstyle="round")


def _icon_stop(c, cx, cy, color):
    c.create_rectangle(cx - 3.5, cy - 3.5, cx + 3.5, cy + 3.5, outline=color, width=1.5)


def _icon_folder(c, cx, cy, color):
    c.create_line(cx - 5, cy - 3, cx - 1.5, cy - 3, cx, cy - 1.5, cx + 5, cy - 1.5, cx + 5, cy + 4,
                  cx - 5, cy + 4, cx - 5, cy - 3, fill=color, width=1.4, joinstyle="round")


def _icon_clipboard(c, cx, cy, color):
    c.create_rectangle(cx - 4, cy - 3.5, cx + 4, cy + 5, outline=color, width=1.4)
    c.create_rectangle(cx - 2, cy - 5, cx + 2, cy - 2.5, outline=color, width=1.4)


ICONS = {"download": _icon_download, "stop": _icon_stop,
         "folder": _icon_folder, "clipboard": _icon_clipboard}


# ------------------------------------------------------------------ 圆角部件
class PillButton:
    STYLES = {
        "primary": dict(bg=PRIMARY, fg="#FFFFFF", border=PRIMARY, hover=PRIMARY_HOVER,
                        icon_bg=YELLOW, icon_fg=CHIP_TEXT),
        "primary_off": dict(bg=PRIMARY_OFF, fg="#FFFFFF", border=PRIMARY_OFF, hover=PRIMARY_OFF,
                            icon_bg="#C6B3F3", icon_fg="#FFFFFF"),
        "secondary": dict(bg=BG, fg=CHIP_TEXT, border=BORDER_STRONG, hover="#F0E8FD",
                          icon_bg=CHIP, icon_fg=ACCENT),
        "secondary_off": dict(bg=BG, fg="#B0A6CF", border=BORDER, hover=BG,
                              icon_bg=HEADER_BG, icon_fg="#B0A6CF"),
    }

    def __init__(self, parent, text, command, style="secondary", width=112, height=30,
                 icon=None, font=None, parent_bg=BG):
        self.text, self.command, self.style, self.icon = text, command, style, icon
        self.width, self.height, self.font = width, height, font
        self.clickable = True
        self.canvas = tk.Canvas(parent, width=width, height=height, bg=parent_bg,
                                highlightthickness=0, bd=0, cursor="hand2")
        self.canvas.bind("<Enter>", lambda e: self.draw(True))
        self.canvas.bind("<Leave>", lambda e: self.draw(False))
        self.canvas.bind("<Button-1>", self._click)
        self.draw()

    def _click(self, _event=None):
        if self.clickable and self.command:
            self.command()

    def draw(self, hover=False):
        c, w, h = self.canvas, self.width, self.height
        st = self.STYLES[self.style]
        c.delete("all")
        fill = st["hover"] if (hover and self.clickable) else st["bg"]
        c.create_polygon(rounded_points(1, 1, w - 1, h - 1, h / 2.0), smooth=True,
                         fill=fill, outline=st["border"])
        icon_d = 18 if self.icon else 0
        gap = 6 if self.icon else 0
        text_w = self.font.measure(self.text) if self.font else 8 * len(self.text)
        x = (w - (icon_d + gap + text_w)) / 2.0
        if self.icon:
            cx, cy = x + icon_d / 2.0, h / 2.0
            c.create_oval(cx - 9, cy - 9, cx + 9, cy + 9, fill=st["icon_bg"], outline="")
            ICONS[self.icon](c, cx, cy, st["icon_fg"])
        c.create_text(x + icon_d + gap, h / 2.0, text=self.text, anchor="w",
                      font=self.font, fill=st["fg"])

    def set(self, style=None, clickable=None, text=None):
        if style is not None:
            self.style = style
        if clickable is not None:
            self.clickable = clickable
        if text is not None:
            self.text = text
        self.draw()

    def pack(self, **kw):
        self.canvas.pack(**kw)

    def pack_forget(self):
        self.canvas.pack_forget()


class PillEntry:
    """圆角输入框：Canvas 画底，里面放一个无边框的 Entry。"""

    def __init__(self, parent, variable, width=100, height=30, font=None,
                 justify="center", parent_bg=BG):
        self.w, self.h = width, height
        self.canvas = tk.Canvas(parent, width=width, height=height, bg=parent_bg,
                                highlightthickness=0, bd=0)
        self.entry = tk.Entry(self.canvas, textvariable=variable, font=font, relief="flat",
                              bd=0, highlightthickness=0, bg=FIELD, fg=TEXT,
                              insertbackground=TEXT, justify=justify)
        self._win = self.canvas.create_window(width / 2.0, height / 2.0, window=self.entry,
                                              width=max(10, width - 20))
        self.canvas.bind("<Configure>", self._on_configure)
        self.canvas.bind("<Button-1>", lambda e: self.entry.focus_set())
        self._draw()

    def _on_configure(self, event):
        self.w, self.h = event.width, event.height
        self._draw()

    def _draw(self):
        c = self.canvas
        c.delete("bg")
        c.create_polygon(rounded_points(1, 1, self.w - 1, self.h - 1, self.h / 2.0), smooth=True,
                         fill=FIELD, outline=BORDER, tags="bg")
        c.tag_lower("bg")
        c.coords(self._win, self.w / 2.0, self.h / 2.0)
        c.itemconfigure(self._win, width=max(10, self.w - 24))

    def pack(self, **kw):
        self.canvas.pack(**kw)


class PillDropdown:
    """折叠下拉框：平时只显示当前选项，点击后弹出选项列表。"""

    def __init__(self, parent, values, variable, width=104, height=30, font=None, parent_bg=BG):
        self.values, self.var, self.font = values, variable, font
        self.w, self.h = width, height
        self.canvas = tk.Canvas(parent, width=width, height=height, bg=parent_bg,
                                highlightthickness=0, bd=0, cursor="hand2")
        self.canvas.bind("<Button-1>", self._popup)
        variable.trace_add("write", lambda *_: self.draw())
        self.draw()

    def draw(self):
        c, w, h = self.canvas, self.w, self.h
        c.delete("all")
        c.create_polygon(rounded_points(1, 1, w - 1, h - 1, h / 2.0), smooth=True,
                         fill=FIELD, outline=BORDER_STRONG)
        c.create_text(12, h / 2.0, text=self.var.get(), anchor="w", font=self.font, fill=BADGE_TEXT)
        cx, cy = w - 14, h / 2.0
        c.create_line(cx - 4, cy - 2, cx, cy + 2, cx + 4, cy - 2, fill=TEXT2, width=1.6,
                      capstyle="round", joinstyle="round")

    def _popup(self, _event=None):
        menu = tk.Menu(self.canvas, tearoff=0)
        for value in self.values:
            menu.add_radiobutton(label=value, variable=self.var, value=value)
        x = self.canvas.winfo_rootx()
        y = self.canvas.winfo_rooty() + self.canvas.winfo_height() + 2
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def pack(self, **kw):
        self.canvas.pack(**kw)


class Chip:
    def __init__(self, parent, text, bg, fg, font, parent_bg):
        w = font.measure(text) + 24
        self.canvas = tk.Canvas(parent, width=w, height=22, bg=parent_bg,
                                highlightthickness=0, bd=0)
        self.canvas.create_polygon(rounded_points(0, 0, w, 22, 11), smooth=True, fill=bg, outline="")
        self.canvas.create_text(w / 2.0, 11, text=text, font=font, fill=fg)

    def pack(self, **kw):
        self.canvas.pack(**kw)


class ProgressBar:
    def __init__(self, parent, parent_bg=BG):
        self.value = 0.0
        self.canvas = tk.Canvas(parent, height=8, bg=parent_bg, highlightthickness=0, bd=0)
        self.canvas.bind("<Configure>", lambda e: self.draw())

    def set(self, value):
        self.value = max(0.0, min(100.0, float(value)))
        self.draw()

    def draw(self):
        c = self.canvas
        w = max(c.winfo_width(), 10)
        c.delete("all")
        c.create_polygon(rounded_points(0, 0, w, 8, 4), smooth=True, fill=HEADER_BG, outline="")
        if self.value > 0:
            fw = max(8, w * self.value / 100.0)
            c.create_polygon(rounded_points(0, 0, fw, 8, 4), smooth=True, fill=PRIMARY, outline="")

    def pack(self, **kw):
        self.canvas.pack(**kw)


# ------------------------------------------------------------------ 粘贴区（空状态）
class PasteZone:
    def __init__(self, parent, on_click, font, small_font, bold_font):
        self.fonts = (font, small_font, bold_font)
        self.canvas = tk.Canvas(parent, height=210, bg=FIELD, highlightthickness=0, bd=0)
        self.button = PillButton(self.canvas, "从剪贴板粘贴", on_click, style="secondary",
                                 width=136, icon="clipboard", font=font, parent_bg=FIELD)
        self._win = self.canvas.create_window(0, 0, window=self.button.canvas)
        self.canvas.bind("<Configure>", lambda e: self.draw())

    def draw(self):
        c = self.canvas
        w, h = max(c.winfo_width(), 200), max(c.winfo_height(), 150)
        font, small, bold = self.fonts
        c.delete("zone")
        c.create_polygon(rounded_points(2, 2, w - 2, h - 2, 16), smooth=True, fill=FIELD,
                         outline=BORDER_STRONG, dash=(5, 4), tags="zone")
        cx, cy = w / 2.0, h / 2.0
        top = cy - 62
        c.create_oval(cx - 22, top - 22, cx + 22, top + 22, fill="#E8DCFC", outline="", tags="zone")
        c.create_rectangle(cx - 8, top - 9, cx + 8, top + 11, outline=ACCENT, width=2, tags="zone")
        c.create_rectangle(cx - 4, top - 12, cx + 4, top - 6, outline=ACCENT, width=2,
                           fill="#E8DCFC", tags="zone")
        c.create_text(cx, cy + 2, text="把链接粘贴到这里", font=bold, fill=TEXT, tags="zone")
        c.create_text(cx, cy + 24, text="支持 Google Drive 和 Facebook Reels 链接",
                      font=small, fill=TEXT3, tags="zone")
        c.create_text(cx, cy + 44, text="也可以直接按 %s 粘贴，可以多次粘贴" % SHORTCUT,
                      font=small, fill=TEXT3, tags="zone")
        c.coords(self._win, cx, cy + 76)
        c.tag_lower("zone")

    def pack(self, **kw):
        self.canvas.pack(**kw)

    def pack_forget(self):
        self.canvas.pack_forget()


# ------------------------------------------------------------------ 链接格子（滚动容器）
class CardList:
    STATUS_STYLE = {"": TEXT3, "等待": TEXT3, "下载中": RUN, "完成": OK, "失败": ERR}

    def __init__(self, parent, fonts):
        self.font, self.small, self.bold, self.mono = fonts
        self.frame = tk.Frame(parent, bg="#FFFFFF", highlightthickness=1,
                              highlightbackground=BORDER_STRONG, bd=0)
        self.bar = tk.Frame(self.frame, bg=FIELD)
        self.bar.pack(side="top", fill="x")
        self.hint = tk.Label(self.bar, bg=FIELD, fg=TEXT3, font=self.small)
        self.body = tk.Frame(self.frame, bg="#FFFFFF")
        self.body.pack(side="top", fill="both", expand=True)
        self.canvas = tk.Canvas(self.body, height=250, bg="#FFFFFF", highlightthickness=0, bd=0)
        self.scroll = ttk.Scrollbar(self.body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.scroll.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self._chips = []
        self.entries, self.titles = [], []
        self.status = {}
        self._status_items, self._card_pos, self._total = {}, {}, 0
        self.canvas.bind("<Configure>", lambda e: self.relayout())
        self.canvas.bind("<Enter>", self._bind_wheel)
        self.canvas.bind("<Leave>", self._unbind_wheel)

    # --- 顶部数量标签
    def set_summary(self, series, singles, files, groups):
        for chip in self._chips:
            chip.canvas.destroy()
        self._chips = []
        specs = []
        if series:
            specs.append(("%d 个系列" % series, YELLOW, YELLOW_TEXT))
        if singles:
            specs.append(("%d 个单文件" % singles, CHIP, CHIP_TEXT))
        specs.append(("共 %d 个文件" % files, CHIP, CHIP_TEXT))
        for text, bg, fg in specs:
            chip = Chip(self.bar, text, bg, fg, self.small, FIELD)
            chip.pack(side="left", padx=(10, 0), pady=8)
            self._chips.append(chip)
        self.hint.config(text="共 %d 组，向下滑动查看" % groups)
        self.hint.pack(side="right", padx=12)

    def set_entries(self, entries, titles):
        self.entries, self.titles = entries, titles
        self.relayout()

    def set_status(self, index, state, text=None):
        """state: 等待 / 下载中 / 完成 / 失败 / 空字符串。"""
        self.status[index] = (text if text is not None else state, self.STATUS_STYLE.get(state, TEXT3))
        item = self._status_items.get(index)
        if item is not None:
            self.canvas.itemconfigure(item, text=self.status[index][0], fill=self.status[index][1])

    def reset_status(self):
        self.status = {}
        for index, item in self._status_items.items():
            self.canvas.itemconfigure(item, text="", fill=TEXT3)

    def reveal(self, index):
        pos = self._card_pos.get(index)
        if not pos or self._total <= 0:
            return
        top, bottom = pos
        view_top, view_bottom = self.canvas.yview()
        if top < view_top * self._total or bottom > view_bottom * self._total:
            self.canvas.yview_moveto(max(0.0, (top - 10) / float(self._total)))

    # --- 绘制
    def relayout(self):
        c = self.canvas
        width = max(c.winfo_width(), 300)
        groups = {}
        for e in self.entries:
            groups.setdefault(e["group"], []).append(e)
        order = sorted(groups)
        layout, total = layout_cards([len(groups[g]) for g in order], width)
        c.delete("all")
        self._status_items, self._card_pos, self._total = {}, {}, total
        for g, (x, y, w, h) in zip(order, layout):
            self._draw_card(x, y, w, h, groups[g], self.titles[g])
        c.configure(scrollregion=(0, 0, width, total))

    def _draw_card(self, x, y, w, h, rows, title):
        c = self.canvas
        c.create_polygon(rounded_points(x, y, x + w, y + h, 12), smooth=True,
                         fill=FIELD, outline=BORDER)
        multi = len(rows) > 1
        bw = self.bold.measure(title) + 18
        by = y + CARD_PAD
        c.create_polygon(rounded_points(x + 10, by, x + 10 + bw, by + 20, 10), smooth=True,
                         fill=BADGE if multi else YELLOW, outline="")
        c.create_text(x + 10 + bw / 2.0, by + 10, text=title, font=self.bold,
                      fill=BADGE_TEXT if multi else YELLOW_TEXT)
        c.create_text(x + 10 + bw + 8, by + 10, anchor="w", font=self.small, fill=TEXT2,
                      text=("%d 个文件" % len(rows)) if multi else "单文件")
        row_y = y + CARD_PAD + CARD_HEADER
        label_w = max(self.bold.measure("00-00"), 36) + 8
        for i, e in enumerate(rows):
            cy = row_y + i * ROW_HEIGHT + ROW_HEIGHT / 2.0
            c.create_text(x + 10, cy, text=e["tag"], anchor="w", font=self.bold, fill=ACCENT)
            link = display_link(e["kind"], e["fid"], e["link"]) if e["valid"] else e["link"]
            text, color = self.status.get(e["index"], ("", TEXT3))
            if not e["valid"]:
                text, color = "无法识别", ERR
            status_w = max(self.small.measure("下载中"), self.small.measure("无法识别")) + 6
            c.create_text(x + 10 + label_w, cy, anchor="w", font=self.mono, fill=TEXT2,
                          text=fit_text(self.mono, link, w - 20 - label_w - status_w))
            item = c.create_text(x + w - 10, cy, anchor="e", font=self.small, fill=color, text=text)
            self._status_items[e["index"]] = item
            self._card_pos[e["index"]] = (y, y + h)

    # --- 鼠标滚轮
    def _bind_wheel(self, _event=None):
        self.canvas.bind_all("<MouseWheel>", self._on_wheel)
        self.canvas.bind_all("<Button-4>", self._on_wheel)
        self.canvas.bind_all("<Button-5>", self._on_wheel)

    def _unbind_wheel(self, _event=None):
        self.canvas.unbind_all("<MouseWheel>")
        self.canvas.unbind_all("<Button-4>")
        self.canvas.unbind_all("<Button-5>")

    def _on_wheel(self, event):
        up = getattr(event, "num", 0) == 4 or getattr(event, "delta", 0) > 0
        self.canvas.yview_scroll(-2 if up else 2, "units")

    def pack(self, **kw):
        self.frame.pack(**kw)

    def pack_forget(self):
        self.frame.pack_forget()


# ------------------------------------------------------------------ 主界面
def _pick_font(root, candidates, fallback):
    available = set(tkfont.families(root))
    for name in candidates:
        if name in available:
            return name
    return fallback


class App:
    def __init__(self, backend):
        if tk is None:
            raise RuntimeError("当前 Python 没有 tkinter")
        self.b = backend
        self.root = tk.Tk()
        try:
            self._build()
        except Exception:
            try:
                self.root.destroy()
            except Exception:
                pass
            raise

    # ---------------------------------------------------------- 构建
    def _build(self):
        root = self.root
        root.title("媒体下载器")
        root.configure(bg=BG)
        root.geometry("700x740")
        root.minsize(640, 660)

        family = _pick_font(root, ["PingFang SC", "Microsoft YaHei UI", "Microsoft YaHei",
                                   "Noto Sans CJK SC", "Source Han Sans SC", "WenQuanYi Micro Hei"],
                            "TkDefaultFont")
        mono = _pick_font(root, ["Menlo", "Consolas", "DejaVu Sans Mono", "Courier New"],
                          "TkFixedFont")
        self.f = tkfont.Font(root=root, family=family, size=-13)
        self.f_small = tkfont.Font(root=root, family=family, size=-12)
        self.f_bold = tkfont.Font(root=root, family=family, size=-13, weight="bold")
        self.f_title = tkfont.Font(root=root, family=family, size=-15, weight="bold")
        self.f_mono = tkfont.Font(root=root, family=mono, size=-12)

        self.start_var = tk.StringVar(value="1")
        self.pad_var = tk.StringVar(value=PAD_CHOICES[1])
        self.out_var = tk.StringVar(value=self.b.initial_out_dir())
        self.status_var = tk.StringVar(value="就绪")

        self.raw_lines = None
        self.plan = None
        self.ok_set = set()
        self.running = False
        self.stop_requested = False
        self.done_cache = {}      # 已下载过的链接，继续下载时不再重复请求
        self._last_key_paste = 0.0

        self._build_header()
        self._build_bottom()
        self._build_top()
        self._show_empty()

        self.start_var.trace_add("write", lambda *_: self._on_settings_changed())
        self.pad_var.trace_add("write", lambda *_: self._on_settings_changed())
        # Mac 上 ⌘V 常以 <<Paste>> 事件送达；其余系统用 Ctrl+V。全部绑定，并在处理里防重复。
        for sequence in ("<<Paste>>", "<Control-v>", "<Control-V>", "<Command-v>", "<Command-V>"):
            try:
                root.bind(sequence, self._on_paste_key)
            except tk.TclError:
                pass
        root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_header(self):
        bar = tk.Frame(self.root, bg=HEADER_BG)
        bar.pack(side="top", fill="x")
        logo = tk.Canvas(bar, width=34, height=34, bg=HEADER_BG, highlightthickness=0, bd=0)
        logo.create_polygon(rounded_points(1, 1, 33, 33, 11), smooth=True, fill="#FFFFFF", outline=BORDER)
        logo.create_line(*cloud_outline(0.26, 4, 4), fill=LOGO, width=2.2,
                         capstyle="round", joinstyle="round")
        logo.create_polygon(*logo_arrow(0.26, 4, 4), fill=YELLOW, outline=LOGO, width=1.1,
                            joinstyle="round")
        logo.pack(side="left", padx=(16, 10), pady=12)
        tk.Label(bar, text="媒体下载器", bg=HEADER_BG, fg=TEXT, font=self.f_title).pack(side="left")
        tk.Frame(self.root, bg=BORDER, height=1).pack(side="top", fill="x")

    def _build_bottom(self):
        """窗口缩小时优先保证底部控件可见，所以先从下往上摆放。"""
        pad = 16
        buttons = tk.Frame(self.root, bg=BG)
        buttons.pack(side="bottom", fill="x", padx=pad, pady=(10, 14))
        self.btn_start = PillButton(buttons, "开始下载", self._on_start, style="primary_off",
                                    icon="download", font=self.f)
        self.btn_stop = PillButton(buttons, "停止", self._on_stop, style="secondary_off",
                                   icon="stop", font=self.f)
        self.btn_stop.set(clickable=False)
        self.btn_open = PillButton(buttons, "打开文件夹", self._on_open_folder, style="secondary",
                                   icon="folder", font=self.f)
        self.btn_start.pack(side="left")
        self.btn_stop.pack(side="left", padx=(8, 0))
        self.btn_open.pack(side="right")

        log_frame = tk.Frame(self.root, bg=BORDER, padx=1, pady=1)
        log_frame.pack(side="bottom", fill="x", padx=pad)
        self.log = tk.Text(log_frame, height=4, wrap="word", font=self.f_mono, bg=FIELD,
                           fg=TEXT2, relief="flat", bd=0, highlightthickness=0, padx=10, pady=8,
                           state="disabled")
        self.log.tag_configure("hint", foreground=TEXT3)
        self.log.pack(side="left", fill="x", expand=True)
        log_scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side="right", fill="y")
        self._log_hint()

        self.progress = ProgressBar(self.root)
        self.progress.pack(side="bottom", fill="x", padx=pad, pady=(6, 10))
        tk.Label(self.root, textvariable=self.status_var, bg=BG, fg=TEXT, font=self.f,
                 anchor="w", justify="left", wraplength=640).pack(side="bottom", fill="x", padx=pad)

        row = tk.Frame(self.root, bg=BG)
        row.pack(side="bottom", fill="x", padx=pad, pady=(12, 12))
        tk.Label(row, text="2. 起始序号", bg=BG, fg=TEXT, font=self.f_bold).pack(side="left")
        PillEntry(row, self.start_var, width=44, font=self.f, parent_bg=BG).pack(side="left", padx=(8, 0))
        tk.Label(row, text="位数", bg=BG, fg=TEXT, font=self.f).pack(side="left", padx=(12, 0))
        PillDropdown(row, PAD_CHOICES, self.pad_var, width=104, font=self.f, parent_bg=BG).pack(
            side="left", padx=(8, 0))
        tk.Label(row, text="3. 保存到", bg=BG, fg=TEXT, font=self.f_bold).pack(side="left", padx=(14, 0))
        self.btn_choose = PillButton(row, "选择…", self._on_choose_folder, style="secondary",
                                     width=60, font=self.f)
        self.btn_choose.pack(side="right")
        PillEntry(row, self.out_var, width=120, font=self.f, justify="left", parent_bg=BG).pack(
            side="left", padx=(8, 8), fill="x", expand=True)

    def _build_top(self):
        pad = 16
        head = tk.Frame(self.root, bg=BG)
        head.pack(side="top", fill="x", padx=pad, pady=(14, 0))
        tk.Label(head, text="1. 粘贴链接", bg=BG, fg=TEXT, font=self.f_bold).pack(side="left")
        tk.Label(head, text="每行一个；同一系列的链接会自动放进同一个格子", bg=BG, fg=TEXT2,
                 font=self.f_small).pack(side="left", padx=(8, 0))
        self.btn_clear = PillButton(head, "清空", self._on_clear, style="secondary",
                                    width=56, height=26, font=self.f_small)

        self.stage = tk.Frame(self.root, bg=BG)
        self.stage.pack(side="top", fill="both", expand=True, padx=pad, pady=(8, 0))
        self.zone = PasteZone(self.stage, self._on_paste_click, self.f, self.f_small, self.f_bold)
        self.cards = CardList(self.stage, (self.f, self.f_small, self.f_bold, self.f_mono))

    # ---------------------------------------------------------- 状态切换
    def _show_empty(self):
        self.cards.pack_forget()
        self.zone.pack(fill="both", expand=True)
        self.btn_clear.pack_forget()
        self.plan = None
        self.raw_lines = None
        self.ok_set = set()
        self.done_cache = {}
        self.progress.set(0)
        self.status_var.set("就绪")
        self._refresh_start_button()

    def _show_cards(self):
        self.zone.pack_forget()
        self.cards.pack(fill="both", expand=True)
        self.btn_clear.pack(side="right")

    def _refresh_start_button(self):
        if self.running:
            self.btn_start.set(style="primary_off", clickable=False)
            self.btn_stop.set(style="secondary", clickable=True)
            return
        self.btn_stop.set(style="secondary_off", clickable=False)
        pending = self._pending_items()
        label = "继续下载" if (self.ok_set and pending) else "开始下载"
        self.btn_start.set(style="primary" if self.plan else "primary_off",
                           clickable=True, text=label)

    def _pending_items(self):
        if not self.plan:
            return []
        return [it for it in self.plan["items"] if it["index"] not in self.ok_set]

    # ---------------------------------------------------------- 粘贴与识别
    def _on_paste_click(self):
        if self.running:
            self.status_var.set("正在下载，下载结束或停止后才能继续粘贴")
            return
        try:
            text = self.root.clipboard_get()
        except tk.TclError:
            text = ""
        if not text.strip():
            messagebox.showinfo("提示", "剪贴板里没有文字。请先复制链接，再粘贴。")
            return
        self.add_text(text)

    def _on_paste_key(self, _event=None):
        try:
            focus = self.root.focus_get()
        except Exception:
            focus = None
        if isinstance(focus, tk.Entry):
            return  # 在输入框里按粘贴，交给输入框自己处理
        now = time.monotonic()
        if now - self._last_key_paste < 0.3:
            return  # 同一次按键可能同时触发 <<Paste>> 和 Ctrl+V，只处理一次
        self._last_key_paste = now
        self._on_paste_click()

    def _canonical(self, lines):
        """把一次粘贴的内容整理成引号完全配对的行，避免上一次没闭合的引号吞掉下一次粘贴。"""
        out = []
        for group in self.b._group_links(lines):
            if len(group) == 1:
                out.append(group[0])
            else:
                out.append('"' + group[0])
                out.extend(group[1:-1])
                out.append(group[-1] + '"')
        return out

    def add_text(self, text):
        """接收粘贴的文字：第一次粘贴建立列表，之后的粘贴接在后面，编号继续往下排。"""
        lines = self._canonical(text.splitlines())
        combined = (self.raw_lines or []) + lines
        plan = self._make_plan(combined) if lines else None
        before = len(self.plan["items"]) if self.plan else 0
        if plan is None or len(plan["items"]) <= before:
            messagebox.showwarning("提示", "没有识别到可用的链接。请确认复制的是 Google Drive 或 Facebook Reels 链接。")
            return
        first = self.plan is None
        added = len(plan["items"]) - before
        self.raw_lines = combined
        if first:
            self.ok_set = set()
            self.progress.set(0)
        self._apply_plan(plan, keep_status=not first)
        self._show_cards()
        if not first:
            self.cards.canvas.yview_moveto(1.0)
        self.status_var.set("已添加 %d 个文件，共 %d 个；按 %s 可继续粘贴，或点击“%s”"
                            % (added, len(plan["items"]), SHORTCUT,
                               "继续下载" if self.ok_set else "开始下载"))
        self._refresh_start_button()

    load_text = add_text

    def _on_clear(self):
        if self.running:
            self.status_var.set("正在下载，请先点“停止”再清空")
            return
        pending = self._pending_items()
        if pending and not messagebox.askyesno(
                "清空", "要清空已粘贴的内容吗？\n还有 %d 个文件没有下载。" % len(pending)):
            return
        self._show_empty()

    def _start_value(self):
        try:
            return int(self.start_var.get().strip())
        except ValueError:
            return None

    def _make_plan(self, lines):
        start = self._start_value()
        return self.b.build_plan(lines, 1 if start is None else start, "",
                                 PAD_VALUES.get(self.pad_var.get(), 0))

    def _apply_plan(self, plan, keep_status=False):
        self.plan = plan
        if not keep_status:
            self.cards.reset_status()
        series, singles, files = summarize(plan)
        self.cards.set_summary(series, singles, files, len(plan["group_titles"]))
        self.cards.set_entries(plan["entries"], plan["group_titles"])

    def _on_settings_changed(self):
        if self.running or self.raw_lines is None:
            return
        plan = self._make_plan(self.raw_lines)
        if plan is None:
            return
        self.ok_set = set()  # 命名规则变了，之前的完成标记不再适用
        self.done_cache = {}
        self._apply_plan(plan)
        self._refresh_start_button()

    # ---------------------------------------------------------- 选择/打开文件夹
    def _on_choose_folder(self):
        chosen = filedialog.askdirectory(initialdir=self.out_var.get() or None, title="选择保存文件夹")
        if chosen:
            self.out_var.set(chosen)
            self.b.save_settings({"out_dir": chosen})

    def _on_open_folder(self):
        path = self.out_var.get().strip()
        if not os.path.isdir(path):
            messagebox.showinfo("提示", "保存文件夹还不存在，下载后会自动创建。")
            return
        self.b._open_folder(path)

    # ---------------------------------------------------------- 日志
    def _log_hint(self):
        self._log_write("点击“开始下载”后，这里会显示每个文件的结果：\n[成功] 序号 01-1 -> 01-1.jpg",
                        tag="hint", clear=True)

    def _log_clear(self):
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _log_write(self, text, tag=None, clear=False):
        self.log.configure(state="normal")
        if clear:
            self.log.delete("1.0", "end")
        self.log.insert("end", text + "\n", tag or ())
        self.log.see("end")
        self.log.configure(state="disabled")

    # ---------------------------------------------------------- 下载
    def _on_stop(self):
        if self.running:
            self.stop_requested = True
            self.status_var.set("正在停止 ...")

    def _on_close(self):
        self.stop_requested = True
        self.root.destroy()

    def _on_start(self):
        if self.running:
            return
        if not self.plan:
            messagebox.showwarning("提示", "请先粘贴至少一个链接。")
            return
        start = self._start_value()
        if start is None:
            messagebox.showerror("错误", "起始序号必须是整数。")
            return
        pending = self._pending_items()
        if not pending:
            if not messagebox.askyesno("提示", "这批文件已经全部下载完成。\n要重新下载一遍吗？（同名文件会自动加上 (1)，不会覆盖）"):
                return
            self.ok_set = set()
            self.cards.reset_status()
            pending = self._pending_items()
        if any(it["kind"] == "drive" for it in pending) and not self.b.check_certifi():
            messagebox.showerror("需要更新依赖", "未找到足够新的安全证书依赖，已停止下载。\n请先安装 requirements-runtime.txt。")
            return
        if any(it["kind"] == "facebook" for it in pending) and not self.b.check_yt_dlp():
            messagebox.showerror("需要更新依赖", "Facebook 下载依赖缺失或过旧，已停止下载。\n请先安装 requirements-facebook.txt。")
            return
        out_dir = self.out_var.get().strip() or os.path.join(os.path.expanduser("~"), "Downloads")
        try:
            os.makedirs(out_dir, exist_ok=True)
        except Exception as e:
            messagebox.showerror("错误", "无法创建保存文件夹：%s" % e)
            return
        self.b.save_settings({"out_dir": out_dir})

        self.running = True
        self.stop_requested = False
        self._refresh_start_button()
        for it in pending:
            self.cards.set_status(it["index"], "等待")
        self.progress.set(0)
        self._log_clear()
        threading.Thread(target=self._worker, args=(pending, out_dir), daemon=True).start()

    def _worker(self, items, out_dir):
        ui = self.root.after
        total = len(items)
        started = {"n": 0}  # status_cb 按顺序对每个文件调用一次

        def on_status(msg):
            index = items[min(started["n"], total - 1)]["index"]
            started["n"] += 1

            def mark():
                self.status_var.set(msg)
                self.cards.set_status(index, "下载中")
                self.cards.reveal(index)
            ui(0, mark)

        def on_file_progress(i, n, done, size):
            frac = (done / size) if size else 0
            ui(0, lambda: self.progress.set((i + frac) / max(n, 1) * 100))

        def on_overall(done, n):
            ui(0, lambda: self.progress.set(done / max(n, 1) * 100))

        def on_result(item, success, info):
            ui(0, lambda: self._record_result(item, success, info))

        def confirm_svg():
            box = {"answer": False}
            ready = threading.Event()

            def ask():
                box["answer"] = messagebox.askyesno(
                    "确认下载 SVG", "这个文件是 SVG 矢量图，可能包含脚本。\n只有确认来源可信时才建议下载，要继续吗？")
                ready.set()
            ui(0, ask)
            ready.wait()
            return box["answer"]

        try:
            count, results = self.b.run_plan(
                items, out_dir, status_cb=on_status, file_progress_cb=on_file_progress,
                overall_progress_cb=on_overall, stop_flag=lambda: self.stop_requested,
                max_bytes=self.b.DEFAULT_MAX_BYTES, svg_confirm=confirm_svg, delay=1.5,
                result_cb=on_result, cache=self.done_cache)
            ui(0, lambda: self._finish(count, total, out_dir, results))
        except Exception as e:  # 防止后台线程异常导致界面一直停在“下载中”
            ui(0, lambda: self._crash(e))

    def _record_result(self, item, success, info):
        index = item["index"]
        if success:
            self.ok_set.add(index)
            self.cards.set_status(index, "完成", "✓ 完成")
            self._log_write("[成功] 序号 %s -> %s" % (item["tag"], os.path.basename(info)))
        elif info == self.b._NOT_RUN_MESSAGE:
            self.cards.set_status(index, "等待", "未下载")
            self._log_write("[未下载] 序号 %s" % item["tag"])
        else:
            self.cards.set_status(index, "失败", "✗ 失败")
            self._log_write("[失败] 序号 %s -> %s" % (item["tag"], info))

    def _finish(self, count, total, out_dir, results):
        self.running = False
        for it in self._pending_items():
            index = it["index"]
            if self.cards.status.get(index, ("",))[0] in ("等待", "下载中"):
                self.cards.set_status(index, "等待", "")
        remaining = len(self._pending_items())
        if self.stop_requested:
            msg = "已停止：本次成功 %d 个，还剩 %d 个未完成" % (count, remaining)
        elif remaining:
            msg = "本次成功 %d 个，还有 %d 个没有完成；可以点击“继续下载”重试" % (count, remaining)
        else:
            msg = "全部完成：成功 %d 个，保存到 %s" % (count, out_dir)
        self.status_var.set(msg)
        self.progress.set(100 if not remaining else self.progress.value)
        self._refresh_start_button()

    def _crash(self, error):
        self.running = False
        self.status_var.set("下载过程中出现意外错误：%s" % error)
        self._log_write("[错误] %s" % error)
        self._refresh_start_button()

    def run(self):
        self.root.mainloop()
