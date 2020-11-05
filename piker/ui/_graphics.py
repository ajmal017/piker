"""
Chart graphics for displaying a slew of different data types.
"""
# import time
from typing import List, Optional, Tuple

import numpy as np
import pyqtgraph as pg
# from numba import jit, float64, optional, int64
from PyQt5 import QtCore, QtGui
from PyQt5.QtCore import QLineF, QPointF

# from .._profile import timeit
from ._style import _xaxis_at, hcolor, _font
from ._axes import YAxisLabel, XAxisLabel, YSticky


# XXX: these settings seem to result in really decent mouse scroll
# latency (in terms of perceived lag in cross hair) so really be sure
# there's an improvement if you want to change it.
_mouse_rate_limit = 60  # calc current screen refresh rate?
_debounce_delay = 1 / 2e3
_ch_label_opac = 1


class LineDot(pg.CurvePoint):

    def __init__(
        self,
        curve: pg.PlotCurveItem,
        index: int,
        pos=None,
        size: int = 2,  # in pxs
        color: str = 'default_light',
    ) -> None:
        pg.CurvePoint.__init__(
            self,
            curve,
            index=index,
            pos=pos,
            rotate=False,
        )

        # TODO: get pen from curve if not defined?
        cdefault = hcolor(color)
        pen = pg.mkPen(cdefault)
        brush = pg.mkBrush(cdefault)

        # presuming this is fast since it's built in?
        dot = self.dot = QtGui.QGraphicsEllipseItem(
            QtCore.QRectF(-size / 2, -size / 2, size, size)
        )
        # if we needed transformable dot?
        # dot.translate(-size*0.5, -size*0.5)
        dot.setPen(pen)
        dot.setBrush(brush)
        dot.setParentItem(self)

        # keep a static size
        self.setFlag(self.ItemIgnoresTransformations)


_corner_anchors = {
    'top': 0,
    'left': 0,
    'bottom': 1,
    'right': 1,
}
# XXX: fyi naming here is confusing / opposite to coords
_corner_margins = {
    ('top', 'left'): (-4, -5),
    ('top', 'right'): (4, -5),
    ('bottom', 'left'): (-4, 5),
    ('bottom', 'right'): (4, 5),
}


class ContentsLabel(pg.LabelItem):
    """Label anchored to a ``ViewBox`` typically for displaying
    datum-wise points from the "viewed" contents.

    """
    def __init__(
        self,
        chart: 'ChartPlotWidget',  # noqa
        anchor_at: str = ('top', 'right'),
        justify_text: str = 'left',
        font_size: Optional[int] = None,
    ) -> None:
        font_size = font_size or _font.font.pixelSize()
        super().__init__(justify=justify_text, size=f'{str(font_size)}px')

        # anchor to viewbox
        self.setParentItem(chart._vb)
        chart.scene().addItem(self)
        self.chart = chart

        v, h = anchor_at
        index = (_corner_anchors[h], _corner_anchors[v])
        margins = _corner_margins[(v, h)]

        self.anchor(itemPos=index, parentPos=index, offset=margins)

    def update_from_ohlc(
        self,
        name: str,
        index: int,
        array: np.ndarray,
    ) -> None:
        # this being "html" is the dumbest shit :eyeroll:
        self.setText(
            "<b>i</b>:{index}<br/>"
            "<b>O</b>:{}<br/>"
            "<b>H</b>:{}<br/>"
            "<b>L</b>:{}<br/>"
            "<b>C</b>:{}<br/>"
            "<b>V</b>:{}".format(
                # *self._array[index].item()[2:8],
                *array[index].item()[2:8],
                name=name,
                index=index,
            )
        )

    def update_from_value(
        self,
        name: str,
        index: int,
        array: np.ndarray,
    ) -> None:
        data = array[index][name]
        self.setText(f"{name}: {data:.2f}")


class CrossHair(pg.GraphicsObject):

    def __init__(
        self,
        linkedsplitcharts: 'LinkedSplitCharts',  # noqa
        digits: int = 0
    ) -> None:
        super().__init__()
        # XXX: not sure why these are instance variables?
        # It's not like we can change them on the fly..?
        self.pen = pg.mkPen(
            color=hcolor('default'),
            style=QtCore.Qt.DashLine,
        )
        self.lines_pen = pg.mkPen(
            color='#a9a9a9',  # gray?
            style=QtCore.Qt.DashLine,
        )
        self.lsc = linkedsplitcharts
        self.graphics = {}
        self.plots = []
        self.active_plot = None
        self.digits = digits
        self._lastx = None

    def add_plot(
        self,
        plot: 'ChartPlotWidget',  # noqa
        digits: int = 0,
    ) -> None:
        # add ``pg.graphicsItems.InfiniteLine``s
        # vertical and horizonal lines and a y-axis label
        vl = plot.addLine(x=0, pen=self.lines_pen, movable=False)

        hl = plot.addLine(y=0, pen=self.lines_pen, movable=False)
        hl.hide()

        yl = YAxisLabel(
            parent=plot.getAxis('right'),
            digits=digits or self.digits,
            opacity=_ch_label_opac,
            bg_color='default',
        )
        yl.hide()  # on startup if mouse is off screen

        # TODO: checkout what ``.sigDelayed`` can be used for
        # (emitted once a sufficient delay occurs in mouse movement)
        px_moved = pg.SignalProxy(
            plot.scene().sigMouseMoved,
            rateLimit=_mouse_rate_limit,
            slot=self.mouseMoved,
            delay=_debounce_delay,
        )
        px_enter = pg.SignalProxy(
            plot.sig_mouse_enter,
            rateLimit=_mouse_rate_limit,
            slot=lambda: self.mouseAction('Enter', plot),
            delay=_debounce_delay,
        )
        px_leave = pg.SignalProxy(
            plot.sig_mouse_leave,
            rateLimit=_mouse_rate_limit,
            slot=lambda: self.mouseAction('Leave', plot),
            delay=_debounce_delay,
        )
        self.graphics[plot] = {
            'vl': vl,
            'hl': hl,
            'yl': yl,
            'px': (px_moved, px_enter, px_leave),
        }
        self.plots.append(plot)

        # Determine where to place x-axis label.
        # Place below the last plot by default, ow
        # keep x-axis right below main chart
        plot_index = -1 if _xaxis_at == 'bottom' else 0

        self.xaxis_label = XAxisLabel(
            parent=self.plots[plot_index].getAxis('bottom'),
            opacity=_ch_label_opac,
            bg_color='default',
        )
        # place label off-screen during startup
        self.xaxis_label.setPos(self.plots[0].mapFromView(QPointF(0, 0)))

    def add_curve_cursor(
        self,
        plot: 'ChartPlotWidget',  # noqa
        curve: 'PlotCurveItem',  # noqa
    ) -> LineDot:
        # if this plot contains curves add line dot "cursors" to denote
        # the current sample under the mouse
        cursor = LineDot(curve, index=len(plot._array))
        plot.addItem(cursor)
        self.graphics[plot].setdefault('cursors', []).append(cursor)
        return cursor

    def mouseAction(self, action, plot):  # noqa
        if action == 'Enter':
            self.active_plot = plot

            # show horiz line and y-label
            self.graphics[plot]['hl'].show()
            self.graphics[plot]['yl'].show()

        else:  # Leave
            self.active_plot = None

            # hide horiz line and y-label
            self.graphics[plot]['hl'].hide()
            self.graphics[plot]['yl'].hide()

    def mouseMoved(
        self,
        evt: 'Tuple[QMouseEvent]',  # noqa
    ) -> None:  # noqa
        """Update horizonal and vertical lines when mouse moves inside
        either the main chart or any indicator subplot.
        """
        pos = evt[0]

        # find position inside active plot
        try:
            # map to view coordinate system
            mouse_point = self.active_plot.mapToView(pos)
        except AttributeError:
            # mouse was not on active plot
            return

        x, y = mouse_point.x(), mouse_point.y()
        plot = self.active_plot

        # update y-range items
        self.graphics[plot]['hl'].setY(y)

        self.graphics[self.active_plot]['yl'].update_label(
            abs_pos=pos, value=y
        )

        # Update x if cursor changed after discretization calc
        # (this saves draw cycles on small mouse moves)
        lastx = self._lastx
        ix = round(x)  # since bars are centered around index

        if ix != lastx:
            for plot, opts in self.graphics.items():

                # move the vertical line to the current "center of bar"
                opts['vl'].setX(ix)

                # update the chart's "contents" label
                plot.update_contents_labels(ix)

                # update all subscribed curve dots
                for cursor in opts.get('cursors', ()):
                    cursor.setIndex(ix)

            # update the label on the bottom of the crosshair
            self.xaxis_label.update_label(

                # XXX: requires:
                # https://github.com/pyqtgraph/pyqtgraph/pull/1418
                # otherwise gobbles tons of CPU..

                # map back to abs (label-local) coordinates
                abs_pos=plot.mapFromView(QPointF(ix, y)),
                value=x,
            )

        self._lastx = ix

    def boundingRect(self):
        try:
            return self.active_plot.boundingRect()
        except AttributeError:
            return self.plots[0].boundingRect()


# @jit(
#     # float64[:](
#     #     float64[:],
#     #     optional(float64),
#     #     optional(int16)
#     # ),
#     nopython=True,
#     nogil=True
# )
def _mk_lines_array(data: List, size: int) -> np.ndarray:
    """Create an ndarray to hold lines graphics objects.
    """
    return np.zeros_like(
        data,
        shape=(int(size), 3),
        dtype=object,
    )


# TODO: `numba` this?

# @jit(
#     # float64[:](
#     #     float64[:],
#     #     optional(float64),
#     #     optional(int16)
#     # ),
#     nopython=True,
#     nogil=True
# )
def bars_from_ohlc(
    data: np.ndarray,
    w: float,
    start: int = 0,
) -> np.ndarray:
    """Generate an array of lines objects from input ohlc data.

    """
    lines = _mk_lines_array(data, data.shape[0])

    for i, q in enumerate(data[start:], start=start):
        open, high, low, close, index = q[
            ['open', 'high', 'low', 'close', 'index']]

        # high -> low vertical (body) line
        if low != high:
            hl = QLineF(index, low, index, high)
        else:
            # XXX: if we don't do it renders a weird rectangle?
            # see below for filtering this later...
            hl = None

        # NOTE: place the x-coord start as "middle" of the drawing range such
        # that the open arm line-graphic is at the left-most-side of
        # the index's range according to the view mapping.

        # open line
        o = QLineF(index - w, open, index, open)
        # close line
        c = QLineF(index, close, index + w, close)

        # indexing here is as per the below comments
        lines[i] = (hl, o, c)

        # XXX: in theory we could get a further speedup by using a flat
        # array and avoiding the call to `np.ravel()` below?
        # lines[3*i:3*i+3] = (hl, o, c)

        # XXX: legacy code from candles custom graphics:
        # if not _tina_mode:
        # else _tina_mode:
        #     self.lines = lines = np.concatenate(
        #       [high_to_low, open_sticks, close_sticks])
        #     use traditional up/down green/red coloring
        #     long_bars = np.resize(Quotes.close > Quotes.open, len(lines))
        #     short_bars = np.resize(
        #       Quotes.close < Quotes.open, len(lines))

        #     ups = lines[long_bars]
        #     downs = lines[short_bars]

        #     # draw "up" bars
        #     p.setPen(self.bull_brush)
        #     p.drawLines(*ups)

        #     # draw "down" bars
        #     p.setPen(self.bear_brush)
        #     p.drawLines(*downs)

    return lines


class BarItems(pg.GraphicsObject):
    """Price range bars graphics rendered from a OHLC sequence.
    """
    sigPlotChanged = QtCore.Signal(object)

    # 0.5 is no overlap between arms, 1.0 is full overlap
    w: float = 0.43
    bars_pen = pg.mkPen(hcolor('bracket'))

    # XXX: tina mode, see below
    # bull_brush = pg.mkPen('#00cc00')
    # bear_brush = pg.mkPen('#fa0000')

    def __init__(
        self,
        # scene: 'QGraphicsScene',  # noqa
        plotitem: 'pg.PlotItem',  # noqa
    ) -> None:
        super().__init__()
        self.last = QtGui.QPicture()
        self.history = QtGui.QPicture()
        # TODO: implement updateable pixmap solution
        self._pi = plotitem
        # self._scene = plotitem.vb.scene()
        # self.picture = QtGui.QPixmap(1000, 300)
        # plotitem.addItem(self.picture)
        # self._pmi = None
        # self._pmi = self._scene.addPixmap(self.picture)

        # XXX: not sure this actually needs to be an array other
        # then for the old tina mode calcs for up/down bars below?
        # lines container
        self.lines = _mk_lines_array([], 50e3)

        # track the current length of drawable lines within the larger array
        self.index: int = 0

    # @timeit
    def draw_from_data(
        self,
        data: np.ndarray,
        start: int = 0,
    ):
        """Draw OHLC datum graphics from a ``np.ndarray``.

        This routine is usually only called to draw the initial history.
        """
        lines = bars_from_ohlc(data, self.w, start=start)

        # save graphics for later reference and keep track
        # of current internal "last index"
        index = len(lines)
        self.lines[:index] = lines
        self.index = index

        # up to last to avoid double draw of last bar
        self.draw_lines(just_history=True, iend=self.index - 1)
        self.draw_lines(iend=self.index)

    # @timeit
    def draw_lines(
        self,
        istart=0,
        iend=None,
        just_history=False,
        # TODO: could get even fancier and only update the single close line?
        lines=None,
    ) -> None:
        """Draw the current line set using the painter.
        """
        if just_history:
            # draw bars for the "history" picture
            iend = iend or self.index - 1
            pic = self.history
        else:
            # draw the last bar
            istart = self.index - 1
            iend = iend or self.index
            pic = self.last

        # use 2d array of lines objects, see conlusion on speed:
        # https://stackoverflow.com/a/60089929
        flat = np.ravel(self.lines[istart:iend])

        # TODO: do this with numba for speed gain:
        # https://stackoverflow.com/questions/58422690/filtering-a-numpy-array-what-is-the-best-approach
        to_draw = flat[np.where(flat != None)]  # noqa

        # pre-computing a QPicture object allows paint() to run much
        # more quickly, rather than re-drawing the shapes every time.
        p = QtGui.QPainter(pic)
        p.setPen(self.bars_pen)

        # TODO: is there any way to not have to pass all the lines every
        # iteration? It seems they won't draw unless it's done this way..
        p.drawLines(*to_draw)
        p.end()

        # XXX: if we ever try using `QPixmap` again...
        # if self._pmi is None:
        #     self._pmi = self.scene().addPixmap(self.picture)
        # else:
        #     self._pmi.setPixmap(self.picture)

        # trigger re-render
        # https://doc.qt.io/qt-5/qgraphicsitem.html#update
        self.update()

    def update_from_array(
        self,
        array: np.ndarray,
        just_history=False,
    ) -> None:
        """Update the last datum's bar graphic from input data array.

        This routine should be interface compatible with
        ``pg.PlotCurveItem.setData()``. Normally this method in
        ``pyqtgraph`` seems to update all the data passed to the
        graphics object, and then update/rerender, but here we're
        assuming the prior graphics havent changed (OHLC history rarely
        does) so this "should" be simpler and faster.
        """
        index = self.index
        length = len(array)
        extra = length - index

        # start_bar_to_update = index - 100

        if extra > 0:
            # generate new graphics to match provided array
            new = array[index:index + extra]
            lines = bars_from_ohlc(new, self.w)
            bars_added = len(lines)
            self.lines[index:index + bars_added] = lines
            self.index += bars_added

            # start_bar_to_update = index - bars_added
            self.draw_lines(just_history=True)
            if just_history:
                return

        # current bar update
        i, o, h, l, last, v = array[-1][
            ['index', 'open', 'high', 'low', 'close', 'volume']
        ]
        assert i == self.index - 1
        body, larm, rarm = self.lines[i]

        # XXX: is there a faster way to modify this?
        rarm.setLine(rarm.x1(), last, rarm.x2(), last)
        # writer is responsible for changing open on "first" volume of bar
        larm.setLine(larm.x1(), o, larm.x2(), o)

        if l != h:  # noqa
            if body is None:
                body = self.lines[index - 1][0] = QLineF(i, l, i, h)
            else:
                # update body
                body.setLine(i, l, i, h)
        else:
            # XXX: h == l -> remove any HL line to avoid render bug
            if body is not None:
                body = self.lines[index - 1][0] = None

        self.draw_lines(just_history=False)

    # @timeit
    def paint(self, p, opt, widget):

        # profiler = pg.debug.Profiler(disabled=False, delayed=False)

        # TODO: use to avoid drawing artefacts?
        # self.prepareGeometryChange()

        # p.setCompositionMode(0)

        # TODO: one thing we could try here is pictures being drawn of
        # a fixed count of bars such that based on the viewbox indices we
        # only draw the "rounded up" number of "pictures worth" of bars
        # as is necesarry for what's in "view". Not sure if this will
        # lead to any perf gains other then when zoomed in to less bars
        # in view.
        p.drawPicture(0, 0, self.history)
        p.drawPicture(0, 0, self.last)

        # TODO: if we can ever make pixmaps work...
        # p.drawPixmap(0, 0, self.picture)
        # self._pmi.setPixmap(self.picture)
        # print(self.scene())

        # profiler('bars redraw:')

    def boundingRect(self):
        # TODO: can we do rect caching to make this faster?

        # Qt docs: https://doc.qt.io/qt-5/qgraphicsitem.html#boundingRect
        # boundingRect _must_ indicate the entire area that will be
        # drawn on or else we will get artifacts and possibly crashing.
        # (in this case, QPicture does all the work of computing the
        # bounding rect for us).

        # compute aggregate bounding rectangle
        lb = self.last.boundingRect()
        hb = self.history.boundingRect()
        return QtCore.QRectF(
            # top left
            QtCore.QPointF(hb.topLeft()),
            # total size
            QtCore.QSizeF(lb.size() + hb.size())
        )


# XXX: when we get back to enabling tina mode for xb
# class CandlestickItems(BarItems):

#     w2 = 0.7
#     line_pen = pg.mkPen('#000000')
#     bull_brush = pg.mkBrush('#00ff00')
#     bear_brush = pg.mkBrush('#ff0000')

#     def _generate(self, p):
#         rects = np.array(
#             [
#                 QtCore.QRectF(
#                   q.id - self.w,
#                   q.open,
#                   self.w2,
#                   q.close - q.open
#               )
#                 for q in Quotes
#             ]
#         )

#         p.setPen(self.line_pen)
#         p.drawLines(
#             [QtCore.QLineF(q.id, q.low, q.id, q.high)
#              for q in Quotes]
#         )

#         p.setBrush(self.bull_brush)
#         p.drawRects(*rects[Quotes.close > Quotes.open])

#         p.setBrush(self.bear_brush)
#         p.drawRects(*rects[Quotes.close < Quotes.open])

class LevelLabel(YSticky):

    line_pen = pg.mkPen(hcolor('bracket'))

    _w_margin = 4
    _h_margin = 3
    level: float = 0

    def __init__(
        self,
        chart,
        *args,
        orient_v: str = 'bottom',
        orient_h: str = 'left',
        **kwargs
    ) -> None:
        super().__init__(chart, *args, **kwargs)

        # orientation around axis options
        self._orient_v = orient_v
        self._orient_h = orient_h
        self._v_shift = {
            'top': 1.,
            'bottom': 0,
            'middle': 1 / 2.
        }[orient_v]

        self._h_shift = {
            'left': -1., 'right': 0
        }[orient_h]

    def update_label(
        self,
        abs_pos: QPointF,  # scene coords
        level: float,  # data for text
        offset: int = 1  # if have margins, k?
    ) -> None:

        # write contents, type specific
        self.set_label_str(level)

        br = self.boundingRect()
        h, w = br.height(), br.width()

        # this triggers ``.pain()`` implicitly?
        self.setPos(QPointF(
            self._h_shift * w - offset,
            abs_pos.y() - (self._v_shift * h) - offset
        ))
        self.update()

        self.level = level

    def set_label_str(self, level: float):
        # this is read inside ``.paint()``
        # self.label_str = '{size} x {level:.{digits}f}'.format(
        self.label_str = '{level:.{digits}f}'.format(
            # size=self._size,
            digits=self.digits,
            level=level
        ).replace(',', ' ')

    def size_hint(self) -> Tuple[None, None]:
        return None, None

    def draw(
        self,
        p: QtGui.QPainter,
        rect: QtCore.QRectF
    ) -> None:
        p.setPen(self.line_pen)

        if self._orient_v == 'bottom':
            lp, rp = rect.topLeft(), rect.topRight()
            # p.drawLine(rect.topLeft(), rect.topRight())
        elif self._orient_v == 'top':
            lp, rp = rect.bottomLeft(), rect.bottomRight()

        p.drawLine(lp.x(), lp.y(), rp.x(), rp.y())


class L1Label(LevelLabel):

    size: float = 0

    text_flags = (
        QtCore.Qt.TextDontClip
        | QtCore.Qt.AlignLeft
    )

    def set_label_str(self, level: float) -> None:
        """Reimplement the label string write to include the level's order-queue's
        size in the text, eg. 100 x 323.3.

        """
        self.label_str = '{size} x {level:,.{digits}f}'.format(
            size=self.size or '?',
            digits=self.digits,
            level=level
        ).replace(',', ' ')


class L1Labels:
    """Level 1 bid ask labels for dynamic update on price-axis.

    """
    max_value: float = '100 x 100 000'

    def __init__(
        self,
        chart: 'ChartPlotWidget',  # noqa
        # level: float,
        digits: int = 2,
        font_size: int = 4,
    ) -> None:
        self.chart = chart

        self.bid_label = L1Label(
            chart=chart,
            parent=chart.getAxis('right'),
            # TODO: pass this from symbol data
            digits=digits,
            opacity=1,
            font_size=font_size,
            bg_color='papas_special',
            fg_color='bracket',
            orient_v='bottom',
        )
        self.bid_label._size_br_from_str(self.max_value)

        self.ask_label = L1Label(
            chart=chart,
            parent=chart.getAxis('right'),
            # TODO: pass this from symbol data
            digits=digits,
            opacity=1,
            font_size=font_size,
            bg_color='papas_special',
            fg_color='bracket',
            orient_v='top',
        )
        self.ask_label._size_br_from_str(self.max_value)


class LevelLine(pg.InfiniteLine):
    def __init__(
        self,
        label: LevelLabel,
        **kwargs,
    ) -> None:
        self.label = label
        super().__init__(**kwargs)
        self.sigPositionChanged.connect(self.set_level)

    def set_level(self, value: float) -> None:
        self.label.update_from_data(0, self.value())


def level_line(
    chart: 'ChartPlogWidget',  # noqa
    level: float,
    digits: int = 1,
    font_size: int = 4,
    **linelabelkwargs
) -> LevelLine:
    """Convenience routine to add a styled horizontal line to a plot.

    """
    label = LevelLabel(
        chart=chart,
        parent=chart.getAxis('right'),
        # TODO: pass this from symbol data
        digits=digits,
        opacity=1,
        font_size=font_size,
        # TODO: make this take the view's bg pen
        bg_color='papas_special',
        fg_color='default',
        **linelabelkwargs
    )
    label.update_from_data(0, level)
    # TODO: can we somehow figure out a max value from the parent axis?
    label._size_br_from_str(label.label_str)

    line = LevelLine(
        label,
        movable=True,
        angle=0,
    )
    line.setValue(level)
    line.setPen(pg.mkPen(hcolor('default')))
    # activate/draw label
    line.setValue(level)

    chart.plotItem.addItem(line)

    return line
