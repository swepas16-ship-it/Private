"""
Fluent Photo Studio – lekki edytor zdjęć/miniatur w PySide6.
Funkcje: warstwy z kryciem i trybami mieszania, narzędzia rysunku/tekstu,
kadrowanie/obrót/odbijanie, zmiana rozmiaru, filtry, preset YouTube 1280x720,
cofanie/przywracanie (Ctrl+Z/Ctrl+Y), ciemny motyw w stylu Fluent.
"""

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from PySide6 import QtCore, QtGui, QtWidgets


APP_NAME = "Fluent Photo Studio"
DEFAULT_CANVAS_SIZE = QtCore.QSize(1280, 720)
MAX_EXPORT_BYTES = 2 * 1024 * 1024  # 2 MB limit for YouTube-friendly export


@dataclass
class Layer:
    name: str
    pixmap: QtGui.QPixmap
    opacity: float = 1.0
    blend_mode: QtGui.QPainter.CompositionMode = QtGui.QPainter.CompositionMode_SourceOver
    visible: bool = True


class LayerList(QtWidgets.QListWidget):
    layer_moved = QtCore.Signal(int, int)
    visibility_toggled = QtCore.Signal(int, bool)
    renamed = QtCore.Signal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self.setDefaultDropAction(QtCore.Qt.MoveAction)
        self.viewport().setAcceptDrops(True)
        self.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.itemChanged.connect(self._on_item_changed)

    def add_layer_item(self, name: str, checked=True):
        item = QtWidgets.QListWidgetItem(name)
        item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable)
        item.setCheckState(QtCore.Qt.Checked if checked else QtCore.Qt.Unchecked)
        self.addItem(item)
        self.setCurrentItem(item)

    def dropEvent(self, event: QtGui.QDropEvent):
        src = self.currentRow()
        super().dropEvent(event)
        dst = self.currentRow()
        if src != dst:
            self.layer_moved.emit(src, dst)

    def _on_item_changed(self, item: QtWidgets.QListWidgetItem):
        row = self.row(item)
        self.visibility_toggled.emit(row, item.checkState() == QtCore.Qt.Checked)
        self.renamed.emit(row, item.text())


class PixmapCommand(QtGui.QUndoCommand):
    def __init__(self, layers: List[Layer], index: int, before: QtGui.QPixmap, after: QtGui.QPixmap, desc: str):
        super().__init__(desc)
        self.layers = layers
        self.index = index
        self.before = before
        self.after = after

    def undo(self):
        self.layers[self.index].pixmap = self.before

    def redo(self):
        self.layers[self.index].pixmap = self.after


class CanvasView(QtWidgets.QGraphicsView):
    tool_changed = QtCore.Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QtWidgets.QGraphicsScene(self))
        self.setRenderHints(
            QtGui.QPainter.Antialiasing | QtGui.QPainter.SmoothPixmapTransform | QtGui.QPainter.TextAntialiasing
        )
        self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
        self.setMouseTracking(True)
        self._zoom = 1.0
        self.layers: List[Layer] = []
        self.active_layer = 0
        self.current_tool = "brush"
        self.brush_color = QtGui.QColor("#ffffff")
        self.brush_size = 12
        self.brush_opacity = 1.0
        self.text_font = QtGui.QFont("Segoe UI", 32)
        self.temp_shape_item = None
        self.start_pos = QtCore.QPointF()
        self.last_pos = QtCore.QPointF()
        self.background_color = QtGui.QColor("#1f1f1f")
        self.init_blank_canvas()

    def init_blank_canvas(self, size: QtCore.QSize = DEFAULT_CANVAS_SIZE):
        self.scene().clear()
        self.layers = [Layer("Tło", QtGui.QPixmap(size))]
        self.layers[0].pixmap.fill(self.background_color)
        self.active_layer = 0
        self._rebuild_scene_items()

    def _rebuild_scene_items(self):
        self.scene().clear()
        for i, layer in enumerate(self.layers):
            item = QtWidgets.QGraphicsPixmapItem(layer.pixmap)
            item.setOpacity(layer.opacity)
            item.setVisible(layer.visible)
            item.setZValue(i)
            self.scene().addItem(item)
        if self.layers:
            self.scene().setSceneRect(self.layers[0].pixmap.rect())

    def set_tool(self, tool: str):
        self.current_tool = tool
        self.tool_changed.emit(tool)

    def set_active_layer(self, index: int):
        if 0 <= index < len(self.layers):
            self.active_layer = index

    def map_to_image(self, event: QtGui.QMouseEvent) -> QtCore.QPoint:
        return self.mapToScene(event.position().toPoint()).toPoint()

    def current_pixmap(self) -> QtGui.QPixmap:
        return self.layers[self.active_layer].pixmap

    def mousePressEvent(self, event: QtGui.QMouseEvent):
        if event.button() == QtCore.Qt.MiddleButton:
            self.setDragMode(self.ScrollHandDrag)
            super().mousePressEvent(event)
            return
        pos = self.map_to_image(event)
        self.start_pos = pos
        self.last_pos = pos

        if self.current_tool == "picker":
            color = self.sample_color(pos)
            if color:
                self.brush_color = color
            return

        if self.current_tool == "text":
            text, ok = QtWidgets.QInputDialog.getText(self, "Tekst", "Wpisz tekst:")
            if ok and text:
                self.draw_text(pos, text)
            return

        if self.current_tool in {"fill", "gradient", "bg_remove"}:
            self.process_click_tool(pos)
            return

        if self.current_tool in {"rect", "ellipse", "line", "arrow"}:
            self.temp_shape_item = QtWidgets.QGraphicsRectItem()
            self.temp_shape_item.setPen(QtGui.QPen(QtGui.QColor("#2f6fed"), 1, QtCore.Qt.DashLine))
            self.scene().addItem(self.temp_shape_item)

        if self.current_tool in {"brush", "eraser"}:
            self.draw_brush(pos, pos, final=False)

    def mouseMoveEvent(self, event: QtGui.QMouseEvent):
        if event.buttons() & QtCore.Qt.MiddleButton:
            super().mouseMoveEvent(event)
            return
        pos = self.map_to_image(event)
        if self.current_tool in {"brush", "eraser"} and event.buttons() & QtCore.Qt.LeftButton:
            self.draw_brush(self.last_pos, pos, final=False)
            self.last_pos = pos
        elif self.temp_shape_item:
            rect = QtCore.QRectF(self.start_pos, pos).normalized()
            self.temp_shape_item.setRect(rect)

    def mouseReleaseEvent(self, event: QtGui.QMouseEvent):
        if event.button() == QtCore.Qt.MiddleButton:
            self.setDragMode(self.NoDrag)
            super().mouseReleaseEvent(event)
            return
        pos = self.map_to_image(event)
        if self.current_tool in {"brush", "eraser"} and event.button() == QtCore.Qt.LeftButton:
            self.draw_brush(self.last_pos, pos, final=True)
        elif self.temp_shape_item:
            rect = self.temp_shape_item.rect()
            self.scene().removeItem(self.temp_shape_item)
            self.temp_shape_item = None
            if self.current_tool == "rect":
                self.draw_rect(rect)
            elif self.current_tool == "ellipse":
                self.draw_ellipse(rect)
            elif self.current_tool in {"line", "arrow"}:
                self.draw_line(self.start_pos, pos, arrow=self.current_tool == "arrow")

    def _paint_on_active(self, painter_fn, desc: str):
        before = self.current_pixmap().copy()
        painter_fn(self.current_pixmap())
        self.scene().items()[len(self.layers) - 1 - self.active_layer].setPixmap(self.current_pixmap())
        return before

    def draw_brush(self, p1: QtCore.QPoint, p2: QtCore.QPoint, final: bool):
        color = QtGui.QColor(self.brush_color)
        color.setAlphaF(self.brush_opacity)
        mode = QtGui.QPainter.CompositionMode_SourceOver if self.current_tool == "brush" else QtGui.QPainter.CompositionMode_Clear

        def paint(pm: QtGui.QPixmap):
            painter = QtGui.QPainter(pm)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            painter.setPen(QtGui.QPen(color, self.brush_size, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin))
            painter.setCompositionMode(mode)
            painter.drawLine(p1, p2)
            painter.end()

        before = self._paint_on_active(paint, "Pędzel")
        if final:
            after = self.current_pixmap().copy()
            self.window().undo_stack.push(PixmapCommand(self.layers, self.active_layer, before, after, "Pędzel"))

    def draw_rect(self, rect: QtCore.QRectF):
        color = QtGui.QColor(self.brush_color)
        color.setAlphaF(self.brush_opacity)

        def paint(pm):
            painter = QtGui.QPainter(pm)
            pen = QtGui.QPen(color, self.brush_size)
            painter.setPen(pen)
            painter.drawRect(rect)
            painter.end()

        before = self._paint_on_active(paint, "Prostokąt")
        self.window().undo_stack.push(PixmapCommand(self.layers, self.active_layer, before, self.current_pixmap().copy(), "Prostokąt"))

    def draw_ellipse(self, rect: QtCore.QRectF):
        color = QtGui.QColor(self.brush_color)
        color.setAlphaF(self.brush_opacity)

        def paint(pm):
            painter = QtGui.QPainter(pm)
            painter.setPen(QtGui.QPen(color, self.brush_size))
            painter.drawEllipse(rect)
            painter.end()

        before = self._paint_on_active(paint, "Elipsa")
        self.window().undo_stack.push(PixmapCommand(self.layers, self.active_layer, before, self.current_pixmap().copy(), "Elipsa"))

    def draw_line(self, start: QtCore.QPointF, end: QtCore.QPointF, arrow=False):
        color = QtGui.QColor(self.brush_color)
        color.setAlphaF(self.brush_opacity)

        def paint(pm):
            painter = QtGui.QPainter(pm)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            pen = QtGui.QPen(color, self.brush_size)
            painter.setPen(pen)
            painter.drawLine(start, end)
            if arrow:
                vec = end - start
                if vec.manhattanLength() > 0:
                    angle = QtCore.qAtan2(vec.y(), vec.x())
                    head_len = 12 + self.brush_size
                    for delta in (-0.5, 0.5):
                        a = angle + delta
                        p = end - QtCore.QPointF(head_len * QtCore.qCos(a), head_len * QtCore.qSin(a))
                        painter.drawLine(end, p)
            painter.end()

        before = self._paint_on_active(paint, "Linia/Strzałka")
        self.window().undo_stack.push(PixmapCommand(self.layers, self.active_layer, before, self.current_pixmap().copy(), "Linia/Strzałka"))

    def draw_text(self, pos: QtCore.QPoint, text: str):
        color = QtGui.QColor(self.brush_color)
        color.setAlphaF(self.brush_opacity)

        def paint(pm):
            painter = QtGui.QPainter(pm)
            painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
            painter.setFont(self.text_font)
            path = QtGui.QPainterPath()
            path.addText(pos, self.text_font, text)
            # cień
            shadow_color = QtGui.QColor(0, 0, 0, 180)
            painter.fillPath(path.translated(3, 3), shadow_color)
            # obrys
            painter.strokePath(path, QtGui.QPen(QtGui.QColor(0, 0, 0, 220), 4))
            painter.fillPath(path, color)
            painter.end()

        before = self._paint_on_active(paint, "Tekst")
        self.window().undo_stack.push(PixmapCommand(self.layers, self.active_layer, before, self.current_pixmap().copy(), "Tekst"))

    def process_click_tool(self, pos: QtCore.QPoint):
        if self.current_tool == "fill":
            self.bucket_fill(pos, tolerance=20)
        elif self.current_tool == "gradient":
            self.gradient_fill()
        elif self.current_tool == "bg_remove":
            self.remove_background(pos, threshold=28)

    def bucket_fill(self, pos: QtCore.QPoint, tolerance: int = 10):
        pm = self.current_pixmap()
        img = pm.toImage().convertToFormat(QtGui.QImage.Format_ARGB32)
        if not img.rect().contains(pos):
            return
        target = QtGui.QColor(img.pixel(pos))
        new = QtGui.QColor(self.brush_color)
        new.setAlphaF(self.brush_opacity)
        if target == new:
            return
        w, h = img.width(), img.height()
        stack = [pos]
        visited = set()
        while stack:
            p = stack.pop()
            if p.x() < 0 or p.y() < 0 or p.x() >= w or p.y() >= h:
                continue
            if (p.x(), p.y()) in visited:
                continue
            visited.add((p.x(), p.y()))
            current = QtGui.QColor(img.pixel(p))
            if self._color_dist(current, target) <= tolerance:
                img.setPixelColor(p, new)
                stack.extend(
                    [
                        QtCore.QPoint(p.x() + 1, p.y()),
                        QtCore.QPoint(p.x() - 1, p.y()),
                        QtCore.QPoint(p.x(), p.y() + 1),
                        QtCore.QPoint(p.x(), p.y() - 1),
                    ]
                )
        before = pm.copy()
        pm.convertFromImage(img)
        self.scene().items()[len(self.layers) - 1 - self.active_layer].setPixmap(pm)
        self.window().undo_stack.push(PixmapCommand(self.layers, self.active_layer, before, pm.copy(), "Wypełnienie"))

    def gradient_fill(self):
        pm = self.current_pixmap()

        def paint(pm_target):
            painter = QtGui.QPainter(pm_target)
            grad = QtGui.QLinearGradient(0, 0, pm.width(), pm.height())
            c1 = self.brush_color
            c2 = QtGui.QColor(self.brush_color)
            c2.setHsv((c2.hue() + 30) % 360, c2.saturation(), c2.value())
            grad.setColorAt(0, c1)
            grad.setColorAt(1, c2)
            painter.fillRect(pm.rect(), grad)
            painter.end()

        before = pm.copy()
        paint(pm)
        self.scene().items()[len(self.layers) - 1 - self.active_layer].setPixmap(pm)
        self.window().undo_stack.push(PixmapCommand(self.layers, self.active_layer, before, pm.copy(), "Gradient"))

    def remove_background(self, pos: QtCore.QPoint, threshold: int = 24):
        pm = self.current_pixmap()
        img = pm.toImage().convertToFormat(QtGui.QImage.Format_ARGB32)
        if not img.rect().contains(pos):
            return
        ref = QtGui.QColor(img.pixel(pos))
        w, h = img.width(), img.height()
        before = pm.copy()
        for y in range(h):
            for x in range(w):
                color = QtGui.QColor(img.pixel(x, y))
                if self._color_dist(color, ref) < threshold:
                    color.setAlpha(0)
                    img.setPixelColor(x, y, color)
        pm.convertFromImage(img)
        self.scene().items()[len(self.layers) - 1 - self.active_layer].setPixmap(pm)
        self.window().undo_stack.push(PixmapCommand(self.layers, self.active_layer, before, pm.copy(), "Usuń tło"))

    @staticmethod
    def _color_dist(c1: QtGui.QColor, c2: QtGui.QColor) -> float:
        return abs(c1.red() - c2.red()) + abs(c1.green() - c2.green()) + abs(c1.blue() - c2.blue())

    @staticmethod
    def _clamp(val):
        return max(0, min(255, int(val)))

    def adjust_current(self, kind: str, value: int):
        """Brightness/contrast/saturation/hue on aktywnej warstwie."""
        pm = self.current_pixmap()
        img = pm.toImage().convertToFormat(QtGui.QImage.Format_ARGB32)
        before = pm.copy()
        w, h = img.width(), img.height()
        for y in range(h):
            for x in range(w):
                c = QtGui.QColor(img.pixel(x, y))
                r, g, b, a = c.red(), c.green(), c.blue(), c.alpha()
                if kind == "brightness":
                    delta = value
                    r = self._clamp(r + delta)
                    g = self._clamp(g + delta)
                    b = self._clamp(b + delta)
                elif kind == "contrast":
                    factor = (259 * (value + 255)) / (255 * (259 - value)) if value != 0 else 1.0
                    r = self._clamp(factor * (r - 128) + 128)
                    g = self._clamp(factor * (g - 128) + 128)
                    b = self._clamp(factor * (b - 128) + 128)
                elif kind == "saturation":
                    col = QtGui.QColor.fromHsv(c.hue(), self._clamp(c.saturation() + value), c.value(), a)
                    r, g, b = col.red(), col.green(), col.blue()
                elif kind == "hue":
                    col = QtGui.QColor.fromHsv((c.hue() + value) % 360, c.saturation(), c.value(), a)
                    r, g, b = col.red(), col.green(), col.blue()
                img.setPixelColor(x, y, QtGui.QColor(r, g, b, a))
        pm.convertFromImage(img)
        self.scene().items()[len(self.layers) - 1 - self.active_layer].setPixmap(pm)
        self.window().undo_stack.push(PixmapCommand(self.layers, self.active_layer, before, pm.copy(), kind))

    def apply_kernel(self, kernel: list[list[int]], factor: int, bias: int, desc: str):
        pm = self.current_pixmap()
        img = pm.toImage().convertToFormat(QtGui.QImage.Format_ARGB32)
        before = pm.copy()
        w, h = img.width(), img.height()
        out = QtGui.QImage(w, h, QtGui.QImage.Format_ARGB32)
        for y in range(h):
            for x in range(w):
                acc_r = acc_g = acc_b = 0
                for ky in range(-1, 2):
                    for kx in range(-1, 2):
                        sx = min(w - 1, max(0, x + kx))
                        sy = min(h - 1, max(0, y + ky))
                        c = QtGui.QColor(img.pixel(sx, sy))
                        k = kernel[ky + 1][kx + 1]
                        acc_r += c.red() * k
                        acc_g += c.green() * k
                        acc_b += c.blue() * k
                r = self._clamp(acc_r / factor + bias)
                g = self._clamp(acc_g / factor + bias)
                b = self._clamp(acc_b / factor + bias)
                out.setPixelColor(x, y, QtGui.QColor(r, g, b, QtGui.QColor(img.pixel(x, y)).alpha()))
        pm.convertFromImage(out)
        self.scene().items()[len(self.layers) - 1 - self.active_layer].setPixmap(pm)
        self.window().undo_stack.push(PixmapCommand(self.layers, self.active_layer, before, pm.copy(), desc))

    def blur(self):
        kernel = [[1, 2, 1], [2, 4, 2], [1, 2, 1]]
        self.apply_kernel(kernel, factor=16, bias=0, desc="Rozmycie")

    def sharpen(self):
        kernel = [[0, -1, 0], [-1, 5, -1], [0, -1, 0]]
        self.apply_kernel(kernel, factor=1, bias=0, desc="Wyostrzenie")

    def denoise(self):
        kernel = [[1, 1, 1], [1, 2, 1], [1, 1, 1]]
        self.apply_kernel(kernel, factor=10, bias=0, desc="Redukcja szumu")

    def sample_color(self, pos: QtCore.QPoint) -> Optional[QtGui.QColor]:
        img = self.flatten_to_image()
        if img.rect().contains(pos):
            return QtGui.QColor(img.pixel(pos))
        return None

    def add_layer(self, name: str):
        base_size = self.layers[0].pixmap.size()
        pm = QtGui.QPixmap(base_size)
        pm.fill(QtCore.Qt.transparent)
        self.layers.insert(0, Layer(name, pm))
        self.active_layer = 0
        self._rebuild_scene_items()

    def delete_layer(self, index: int):
        if len(self.layers) <= 1 or not (0 <= index < len(self.layers)):
            return
        del self.layers[index]
        self.active_layer = min(self.active_layer, len(self.layers) - 1)
        self._rebuild_scene_items()

    def set_opacity(self, index: int, value: int):
        if 0 <= index < len(self.layers):
            self.layers[index].opacity = value / 100
            self.scene().items()[len(self.layers) - 1 - index].setOpacity(self.layers[index].opacity)

    def set_visibility(self, index: int, visible: bool):
        if 0 <= index < len(self.layers):
            self.layers[index].visible = visible
            self.scene().items()[len(self.layers) - 1 - index].setVisible(visible)

    def move_layer(self, src: int, dst: int):
        if src == dst:
            return
        layer = self.layers.pop(src)
        self.layers.insert(dst, layer)
        self._rebuild_scene_items()
        self.active_layer = dst

    def flatten_to_image(self) -> QtGui.QImage:
        if not self.layers:
            return QtGui.QImage()
        base = QtGui.QImage(self.layers[0].pixmap.size(), QtGui.QImage.Format_ARGB32)
        base.fill(QtCore.Qt.transparent)
        painter = QtGui.QPainter(base)
        for layer in reversed(self.layers):
            if not layer.visible:
                continue
            painter.setOpacity(layer.opacity)
            painter.setCompositionMode(layer.blend_mode)
            painter.drawPixmap(0, 0, layer.pixmap)
        painter.end()
        return base

    def apply_transform(self, transform: QtGui.QTransform, desc: str):
        before_imgs = [l.pixmap.copy() for l in self.layers]
        for layer in self.layers:
            layer.pixmap = layer.pixmap.transformed(transform, QtCore.Qt.SmoothTransformation)
        self._rebuild_scene_items()
        self.window().undo_stack.push(
            TransformCommand(self.layers, before_imgs, [l.pixmap.copy() for l in self.layers], desc)
        )

    def resize_canvas(self, size: QtCore.QSize):
        before_imgs = [l.pixmap.copy() for l in self.layers]
        for layer in self.layers:
            new_pm = QtGui.QPixmap(size)
            new_pm.fill(QtCore.Qt.transparent)
            painter = QtGui.QPainter(new_pm)
            painter.drawPixmap(0, 0, layer.pixmap.scaled(size, QtCore.Qt.IgnoreAspectRatio, QtCore.Qt.SmoothTransformation))
            painter.end()
            layer.pixmap = new_pm
        self._rebuild_scene_items()
        self.window().undo_stack.push(
            TransformCommand(self.layers, before_imgs, [l.pixmap.copy() for l in self.layers], "Zmiana rozmiaru")
        )

    def crop_rect(self, rect: QtCore.QRect):
        rect = rect.intersected(self.layers[0].pixmap.rect())
        if rect.isEmpty():
            return
        before = [l.pixmap.copy() for l in self.layers]
        for layer in self.layers:
            layer.pixmap = layer.pixmap.copy(rect)
        self._rebuild_scene_items()
        self.window().undo_stack.push(
            TransformCommand(self.layers, before, [l.pixmap.copy() for l in self.layers], "Kadrowanie")
        )


class TransformCommand(QtGui.QUndoCommand):
    def __init__(self, layers: List[Layer], before: List[QtGui.QPixmap], after: List[QtGui.QPixmap], desc: str):
        super().__init__(desc)
        self.layers = layers
        self.before = before
        self.after = after

    def undo(self):
        for i, pm in enumerate(self.before):
            self.layers[i].pixmap = pm

    def redo(self):
        for i, pm in enumerate(self.after):
            self.layers[i].pixmap = pm


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1440, 900)
        self.undo_stack = QtGui.QUndoStack(self)
        self.current_file: Optional[Path] = None
        # Utwórz canvas przed akcjami, bo akcje korzystają z self.canvas
        self.canvas = CanvasView()
        self._build_actions()
        self._build_ui()
        self._apply_theme()

    def _build_actions(self):
        self.act_open = QtGui.QAction("Otwórz", self, shortcut="Ctrl+O", triggered=self.open_image)
        self.act_save = QtGui.QAction("Zapisz", self, shortcut="Ctrl+S", triggered=self.save_image)
        self.act_export = QtGui.QAction("Eksport", self, shortcut="Ctrl+Shift+S", triggered=self.export_image)
        self.act_undo = self.undo_stack.createUndoAction(self, "Cofnij")
        self.act_undo.setShortcut("Ctrl+Z")
        self.act_redo = self.undo_stack.createRedoAction(self, "Przywróć")
        self.act_redo.setShortcut("Ctrl+Y")
        self.act_youtube = QtGui.QAction("Preset YouTube 1280x720", self, triggered=self.apply_youtube_preset)
        self.act_rotate_left = QtGui.QAction("Obróć -90°", self, triggered=lambda: self.canvas.apply_transform(QtGui.QTransform().rotate(-90), "Obrót -90"))
        self.act_rotate_right = QtGui.QAction("Obróć 90°", self, triggered=lambda: self.canvas.apply_transform(QtGui.QTransform().rotate(90), "Obrót 90"))
        self.act_flip_h = QtGui.QAction("Odbij poziomo", self, triggered=lambda: self.canvas.apply_transform(QtGui.QTransform(-1, 0, 0, 1, self.canvas.layers[0].pixmap.width(), 0), "Odbicie poziome"))
        self.act_flip_v = QtGui.QAction("Odbij pionowo", self, triggered=lambda: self.canvas.apply_transform(QtGui.QTransform(1, 0, 0, -1, 0, self.canvas.layers[0].pixmap.height()), "Odbicie pionowe"))
        self.act_resize = QtGui.QAction("Zmień rozmiar", self, triggered=self.show_resize_dialog)
        self.act_crop = QtGui.QAction("Kadruj", self, triggered=self.show_crop_dialog)
        self.act_brightness = QtGui.QAction("Jasność", self, triggered=lambda: self._ask_adjust("brightness", -100, 100, 0))
        self.act_contrast = QtGui.QAction("Kontrast", self, triggered=lambda: self._ask_adjust("contrast", -128, 128, 0))
        self.act_saturation = QtGui.QAction("Nasycenie", self, triggered=lambda: self._ask_adjust("saturation", -100, 100, 0))
        self.act_hue = QtGui.QAction("Odcień", self, triggered=lambda: self._ask_adjust("hue", -180, 180, 0))
        self.act_sharpen = QtGui.QAction("Wyostrzenie", self, triggered=self.canvas.sharpen)
        self.act_blur = QtGui.QAction("Rozmycie", self, triggered=self.canvas.blur)
        self.act_denoise = QtGui.QAction("Redukcja szumu", self, triggered=self.canvas.denoise)

    def _build_ui(self):
        toolbar = QtWidgets.QToolBar("Opcje narzędzia")
        toolbar.setMovable(False)
        toolbar.addActions([self.act_open, self.act_save, self.act_export])
        toolbar.addSeparator()
        toolbar.addActions([self.act_undo, self.act_redo])
        toolbar.addSeparator()
        toolbar.addAction(self.act_youtube)
        toolbar.addActions([self.act_rotate_left, self.act_rotate_right, self.act_flip_h, self.act_flip_v])
        toolbar.addAction(self.act_resize)
        toolbar.addAction(self.act_crop)
        self.addToolBar(QtCore.Qt.TopToolBarArea, toolbar)

        adjust_bar = QtWidgets.QToolBar("Korekcje")
        adjust_bar.setMovable(False)
        adjust_bar.addActions([self.act_brightness, self.act_contrast, self.act_saturation, self.act_hue, self.act_sharpen, self.act_blur, self.act_denoise])
        self.addToolBar(QtCore.Qt.TopToolBarArea, adjust_bar)

        self.toolbox = QtWidgets.QListWidget()
        tools = [
            ("Zaznaczenie", "select"),
            ("Pędzel", "brush"),
            ("Gumka", "eraser"),
            ("Prostokąt", "rect"),
            ("Elipsa", "ellipse"),
            ("Linia", "line"),
            ("Strzałka", "arrow"),
            ("Tekst", "text"),
            ("Pipeta", "picker"),
            ("Wypełnienie", "fill"),
            ("Gradient", "gradient"),
            ("Usuń tło", "bg_remove"),
            ("Filtr filmowy", "cinematic"),
            ("Filtr HDR", "hdr"),
        ]
        for label, code in tools:
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, code)
            self.toolbox.addItem(item)
        self.toolbox.setCurrentRow(1)
        self.toolbox.setFixedWidth(180)
        self.toolbox.currentItemChanged.connect(self._on_tool_selected)

        self.layers = LayerList()
        self.layers.add_layer_item("Tło")

        add_layer_btn = QtWidgets.QPushButton("Dodaj warstwę")
        add_layer_btn.clicked.connect(lambda: self.canvas.add_layer(f"Warstwa {len(self.canvas.layers)+1}") or self.layers.add_layer_item(f"Warstwa {self.layers.count()+1}"))
        del_layer_btn = QtWidgets.QPushButton("Usuń warstwę")
        del_layer_btn.clicked.connect(self._delete_layer)

        self.opacity_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.opacity_slider.setRange(0, 100)
        self.opacity_slider.setValue(100)
        self.opacity_label = QtWidgets.QLabel("Krycie: 100%")
        self.opacity_slider.valueChanged.connect(self._on_opacity_change)

        blend_box = QtWidgets.QComboBox()
        self.blend_modes = {
            "Normal": QtGui.QPainter.CompositionMode_SourceOver,
            "Multiply": QtGui.QPainter.CompositionMode_Multiply,
            "Screen": QtGui.QPainter.CompositionMode_Screen,
            "Overlay": QtGui.QPainter.CompositionMode_Overlay,
        }
        blend_box.addItems(self.blend_modes.keys())
        blend_box.currentTextChanged.connect(self._on_blend_change)

        props = QtWidgets.QVBoxLayout()
        props.addWidget(QtWidgets.QLabel("Warstwy"))
        props.addWidget(self.layers, 1)
        props.addWidget(add_layer_btn)
        props.addWidget(del_layer_btn)
        props.addSpacing(8)
        props.addWidget(self.opacity_label)
        props.addWidget(self.opacity_slider)
        props.addWidget(QtWidgets.QLabel("Tryb mieszania"))
        props.addWidget(blend_box)
        props.addStretch()

        right_widget = QtWidgets.QWidget()
        right_widget.setLayout(props)
        right_widget.setFixedWidth(240)

        central = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(central)
        layout.addWidget(self.toolbox)
        layout.addWidget(self.canvas, 1)
        layout.addWidget(right_widget)
        self.setCentralWidget(central)
        self.statusBar().showMessage("Ctrl+scroll powiększa, środkowy przycisk przesuwa")

        self.layers.layer_moved.connect(self.canvas.move_layer)
        self.layers.visibility_toggled.connect(self.canvas.set_visibility)
        self.layers.renamed.connect(lambda i, name: self._rename_layer(i, name))
        self.layers.currentRowChanged.connect(self.canvas.set_active_layer)

        color_btn = QtWidgets.QPushButton("Kolor")
        color_btn.clicked.connect(self._choose_color)
        size_spin = QtWidgets.QSpinBox()
        size_spin.setRange(1, 200)
        size_spin.setValue(self.canvas.brush_size)
        size_spin.valueChanged.connect(lambda v: setattr(self.canvas, "brush_size", v))
        size_label = QtWidgets.QLabel("Rozmiar")
        extra = QtWidgets.QToolBar()
        extra.addWidget(color_btn)
        extra.addSeparator()
        extra.addWidget(size_label)
        extra.addWidget(size_spin)
        extra.setMovable(False)
        self.addToolBar(QtCore.Qt.BottomToolBarArea, extra)

    def _apply_theme(self):
        palette = QtGui.QPalette()
        palette.setColor(QtGui.QPalette.Window, QtGui.QColor("#0f1116"))
        palette.setColor(QtGui.QPalette.WindowText, QtCore.Qt.white)
        palette.setColor(QtGui.QPalette.Base, QtGui.QColor("#14171f"))
        palette.setColor(QtGui.QPalette.AlternateBase, QtGui.QColor("#1a1d27"))
        palette.setColor(QtGui.QPalette.ToolTipBase, QtCore.Qt.white)
        palette.setColor(QtGui.QPalette.ToolTipText, QtCore.Qt.white)
        palette.setColor(QtGui.QPalette.Text, QtCore.Qt.white)
        palette.setColor(QtGui.QPalette.Button, QtGui.QColor("#1b1f2a"))
        palette.setColor(QtGui.QPalette.ButtonText, QtCore.Qt.white)
        palette.setColor(QtGui.QPalette.Highlight, QtGui.QColor("#2f6fed"))
        palette.setColor(QtGui.QPalette.HighlightedText, QtCore.Qt.white)
        self.setPalette(palette)
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #0f1116; color: #e9eef7; }
            QListWidget, QGraphicsView { background: #14171f; border: 1px solid #1f2633; }
            QToolBar { background: #0f1116; border: 0; spacing: 6px; padding: 6px; }
            QToolButton { background: #1b1f2a; border: 1px solid #1f2633; padding: 6px 10px; border-radius: 6px; }
            QToolButton:hover { border-color: #2f6fed; }
            QPushButton { background: #1b1f2a; border: 1px solid #1f2633; padding: 6px 10px; border-radius: 6px; }
            QPushButton:hover { border-color: #2f6fed; }
            QSlider::groove:horizontal { height: 6px; background: #1f2633; border-radius: 3px; }
            QSlider::handle:horizontal { width: 16px; background: #2f6fed; border-radius: 8px; margin: -5px 0; }
            QStatusBar { background: #0f1116; border: 0; }
            """
        )

    def _on_tool_selected(self, current: QtWidgets.QListWidgetItem):
        if not current:
            return
        code = current.data(QtCore.Qt.UserRole)
        self.canvas.set_tool(code)
        if code in {"cinematic", "hdr"}:
            self.apply_filter(code)

    def _choose_color(self):
        color = QtWidgets.QColorDialog.getColor(self.canvas.brush_color, self, "Wybierz kolor")
        if color.isValid():
            self.canvas.brush_color = color

    def _on_opacity_change(self, value: int):
        self.opacity_label.setText(f"Krycie: {value}%")
        self.canvas.set_opacity(self.layers.currentRow(), value)

    def _on_blend_change(self, text: str):
        idx = self.layers.currentRow()
        if 0 <= idx < len(self.canvas.layers):
            self.canvas.layers[idx].blend_mode = self.blend_modes[text]

    def _delete_layer(self):
        row = self.layers.currentRow()
        self.canvas.delete_layer(row)
        if row >= 0:
            self.layers.takeItem(row)

    def _rename_layer(self, index: int, name: str):
        if 0 <= index < len(self.canvas.layers):
            self.canvas.layers[index].name = name

    def open_image(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Otwórz obraz", "", "Obrazy (*.png *.jpg *.jpeg *.bmp *.webp)"
        )
        if not path:
            return
        img = QtGui.QImage(path)
        if img.isNull():
            QtWidgets.QMessageBox.warning(self, "Błąd", "Nie udało się wczytać obrazu.")
            return
        pm = QtGui.QPixmap.fromImage(img)
        self.canvas.layers = [Layer(Path(path).name, pm)]
        self.canvas.active_layer = 0
        self.canvas._rebuild_scene_items()
        self.layers.clear()
        self.layers.add_layer_item(Path(path).name)
        self.current_file = Path(path)
        self.statusBar().showMessage(f"Wczytano: {self.current_file.name}")

    def save_image(self):
        if not self.current_file:
            return self.export_image()
        img = self.canvas.flatten_to_image()
        img.save(str(self.current_file))
        self.statusBar().showMessage(f"Zapisano: {self.current_file.name}")

    def _encode_with_limit(self, img: QtGui.QImage, fmt: str) -> tuple[bytes, int]:
        quality = 95
        while quality >= 60:
            buf = QtCore.QBuffer()
            buf.open(QtCore.QIODevice.WriteOnly)
            img.save(buf, fmt, quality)
            data = bytes(buf.data())
            if len(data) <= MAX_EXPORT_BYTES or fmt.lower() == "png":
                return data, quality
            quality -= 5
        return data, quality

    def export_image(self):
        path, selected = QtWidgets.QFileDialog.getSaveFileName(
            self, "Eksportuj", "", "PNG (*.png);;JPG (*.jpg);;WEBP (*.webp);;BMP (*.bmp)"
        )
        if not path:
            return
        img = self.canvas.flatten_to_image()
        fmt = selected.split("*.")[-1].split(")")[0].upper()
        data, quality = self._encode_with_limit(img, fmt)
        Path(path).write_bytes(data)
        self.statusBar().showMessage(f"Eksportowano: {Path(path).name} (jakość {quality})")

    def apply_youtube_preset(self):
        self.canvas.resize_canvas(DEFAULT_CANVAS_SIZE)
        self.apply_filter("cinematic")
        self.statusBar().showMessage("Preset YouTube 1280x720 zastosowany")

    def apply_filter(self, kind: str):
        pm = self.canvas.current_pixmap()
        img = pm.toImage().convertToFormat(QtGui.QImage.Format_ARGB32)
        before = pm.copy()
        for y in range(img.height()):
            for x in range(img.width()):
                c = QtGui.QColor(img.pixel(x, y))
                r, g, b, a = c.red(), c.green(), c.blue(), c.alpha()
                if kind == "cinematic":
                    r = min(255, int(r * 0.95 + 10))
                    g = min(255, int(g * 0.9 + 5))
                    b = min(255, int(b * 1.05 + 15))
                else:
                    r = min(255, int((r / 255) ** 0.8 * 255))
                    g = min(255, int((g / 255) ** 0.8 * 255))
                    b = min(255, int((b / 255) ** 0.8 * 255))
                img.setPixelColor(x, y, QtGui.QColor(r, g, b, a))
        pm.convertFromImage(img)
        self.canvas.scene().items()[len(self.canvas.layers) - 1 - self.canvas.active_layer].setPixmap(pm)
        self.undo_stack.push(PixmapCommand(self.canvas.layers, self.canvas.active_layer, before, pm.copy(), kind))
        self.statusBar().showMessage(f"Filtr {kind} zastosowany")

    def _ask_adjust(self, kind: str, minv: int, maxv: int, default: int):
        val, ok = QtWidgets.QInputDialog.getInt(self, kind.capitalize(), "Wartość:", default, minv, maxv, 1)
        if ok:
            self.canvas.adjust_current(kind, val)

    def show_resize_dialog(self):
        size = self.canvas.layers[0].pixmap.size()
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Zmień rozmiar")
        w_spin = QtWidgets.QSpinBox(); w_spin.setRange(16, 8000); w_spin.setValue(size.width())
        h_spin = QtWidgets.QSpinBox(); h_spin.setRange(16, 8000); h_spin.setValue(size.height())
        lock = QtWidgets.QCheckBox("Zachowaj proporcje"); lock.setChecked(True)
        ratio = size.width() / size.height() if size.height() else 1

        def sync_height(val):
            if lock.isChecked():
                h_spin.blockSignals(True)
                h_spin.setValue(max(1, int(val / ratio)))
                h_spin.blockSignals(False)

        def sync_width(val):
            if lock.isChecked():
                w_spin.blockSignals(True)
                w_spin.setValue(max(1, int(val * ratio)))
                w_spin.blockSignals(False)

        w_spin.valueChanged.connect(sync_height)
        h_spin.valueChanged.connect(sync_width)
        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject)
        form = QtWidgets.QFormLayout(dlg)
        form.addRow("Szerokość", w_spin); form.addRow("Wysokość", h_spin); form.addRow(lock); form.addWidget(btns)
        if dlg.exec():
            self.canvas.resize_canvas(QtCore.QSize(w_spin.value(), h_spin.value()))

    def show_crop_dialog(self):
        size = self.canvas.layers[0].pixmap.size()
        dlg = QtWidgets.QDialog(self); dlg.setWindowTitle("Kadrowanie")
        x_spin = QtWidgets.QSpinBox(); x_spin.setRange(0, size.width()); x_spin.setValue(0)
        y_spin = QtWidgets.QSpinBox(); y_spin.setRange(0, size.height()); y_spin.setValue(0)
        w_spin = QtWidgets.QSpinBox(); w_spin.setRange(1, size.width()); w_spin.setValue(size.width())
        h_spin = QtWidgets.QSpinBox(); h_spin.setRange(1, size.height()); h_spin.setValue(size.height())
        lock = QtWidgets.QCheckBox("Zachowaj proporcje"); lock.setChecked(False)
        ratio = size.width() / size.height() if size.height() else 1

        def sync_h(val):
            if lock.isChecked():
                h_spin.blockSignals(True); h_spin.setValue(max(1, int(val / ratio))); h_spin.blockSignals(False)

        def sync_w(val):
            if lock.isChecked():
                w_spin.blockSignals(True); w_spin.setValue(max(1, int(val * ratio))); w_spin.blockSignals(False)

        w_spin.valueChanged.connect(sync_h); h_spin.valueChanged.connect(sync_w)
        btns = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept); btns.rejected.connect(dlg.reject)
        form = QtWidgets.QFormLayout(dlg)
        form.addRow("X", x_spin); form.addRow("Y", y_spin); form.addRow("Szerokość", w_spin); form.addRow("Wysokość", h_spin); form.addRow(lock); form.addWidget(btns)
        if dlg.exec():
            rect = QtCore.QRect(x_spin.value(), y_spin.value(), w_spin.value(), h_spin.value())
            self.canvas.crop_rect(rect)


def main():
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

