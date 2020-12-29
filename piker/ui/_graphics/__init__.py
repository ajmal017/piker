# piker: trading gear for hackers
# Copyright (C) 2018-present  Tyler Goodlet (in stewardship of piker0)

# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.

# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.

# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.

"""
Chart graphics for displaying a slew of different data types.
"""
import inspect
from typing import List, Optional, Tuple

import numpy as np
import pyqtgraph as pg
from numba import jit, float64, int64  # , optional
# from numba import types as ntypes
from PyQt5 import QtCore, QtGui
from PyQt5.QtCore import QLineF, QPointF

# from .._profile import timeit
# from ..data._source import numba_ohlc_dtype
from .._style import (
    _xaxis_at,
    hcolor,
    _font,
    _down_2_font_inches_we_like,
)
from .._axes import YAxisLabel, XAxisLabel, YSticky


# XXX: these settings seem to result in really decent mouse scroll
# latency (in terms of perceived lag in cross hair) so really be sure
# there's an improvement if you want to change it!
_mouse_rate_limit = 60  # TODO; should we calc current screen refresh rate?
_debounce_delay = 1 / 2e3
_ch_label_opac = 1


# TODO: we need to handle the case where index is outside
# the underlying datums range
class LineDot(pg.CurvePoint):

    def __init__(
        self,
        curve: pg.PlotCurveItem,
        index: int,
        plot: 'ChartPlotWidget',
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
        self._plot = plot

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

    def event(
        self,
        ev: QtCore.QEvent,
    ) -> None:
        # print((ev, type(ev)))
        if not isinstance(ev, QtCore.QDynamicPropertyChangeEvent) or self.curve() is None:
            return False

        # if ev.propertyName() == 'index':
        #     print(ev)
        #     # self.setProperty

        (x, y) = self.curve().getData()
        index = self.property('index')
        # first = self._plot._ohlc[0]['index']
        # first = x[0]
        # i = index - first
        i = index - x[0]
        if i > 0 and i < len(y):
            newPos = (index, y[i])
            QtGui.QGraphicsItem.setPos(self, *newPos)
            return True

        return False


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

    ('bottom', 'left'): (-4, lambda font_size: font_size * 2),
    ('bottom', 'right'): (4, lambda font_size: font_size * 2),
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
        super().__init__(
            justify=justify_text,
            size=f'{str(font_size)}px'
        )

        # anchor to viewbox
        self.setParentItem(chart._vb)
        chart.scene().addItem(self)
        self.chart = chart

        v, h = anchor_at
        index = (_corner_anchors[h], _corner_anchors[v])
        margins = _corner_margins[(v, h)]

        ydim = margins[1]
        if inspect.isfunction(margins[1]):
            margins = margins[0], ydim(font_size)

        self.anchor(itemPos=index, parentPos=index, offset=margins)

    def update_from_ohlc(
        self,
        name: str,
        index: int,
        array: np.ndarray,
    ) -> None:
        # this being "html" is the dumbest shit :eyeroll:
        first = array[0]['index']

        self.setText(
            "<b>i</b>:{index}<br/>"
            "<b>O</b>:{}<br/>"
            "<b>H</b>:{}<br/>"
            "<b>L</b>:{}<br/>"
            "<b>C</b>:{}<br/>"
            "<b>V</b>:{}<br/>"
            "<b>wap</b>:{}".format(
                *array[index - first][
                    ['open', 'high', 'low', 'close', 'volume', 'bar_wap']
                ],
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
        first = array[0]['index']
        if index < array[-1]['index'] and index > first:
            data = array[index - first][name]
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
        # self.setCacheMode(QtGui.QGraphicsItem.DeviceCoordinateCache)

    def add_plot(
        self,
        plot: 'ChartPlotWidget',  # noqa
        digits: int = 0,
    ) -> None:
        # add ``pg.graphicsItems.InfiniteLine``s
        # vertical and horizonal lines and a y-axis label
        vl = plot.addLine(x=0, pen=self.lines_pen, movable=False)
        vl.setCacheMode(QtGui.QGraphicsItem.DeviceCoordinateCache)

        hl = plot.addLine(y=0, pen=self.lines_pen, movable=False)
        hl.setCacheMode(QtGui.QGraphicsItem.DeviceCoordinateCache)
        hl.hide()

        yl = YAxisLabel(
            parent=plot.getAxis('right'),
            digits=digits or self.digits,
            opacity=_ch_label_opac,
            bg_color='default',
        )
        yl.setCacheMode(QtGui.QGraphicsItem.DeviceCoordinateCache)
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
        self.xaxis_label.setCacheMode(QtGui.QGraphicsItem.DeviceCoordinateCache)

    def add_curve_cursor(
        self,
        plot: 'ChartPlotWidget',  # noqa
        curve: 'PlotCurveItem',  # noqa
    ) -> LineDot:
        # if this plot contains curves add line dot "cursors" to denote
        # the current sample under the mouse
        cursor = LineDot(curve, index=plot._ohlc[-1]['index'], plot=plot)
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
                # first = plot._ohlc[0]['index']
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
    size_digits: float = 3

    text_flags = (
        QtCore.Qt.TextDontClip
        | QtCore.Qt.AlignLeft
    )

    def set_label_str(self, level: float) -> None:
        """Reimplement the label string write to include the level's order-queue's
        size in the text, eg. 100 x 323.3.

        """
        self.label_str = '{size:.{size_digits}f} x {level:,.{digits}f}'.format(
            size_digits=self.size_digits,
            size=self.size or '?',
            digits=self.digits,
            level=level
        ).replace(',', ' ')


class L1Labels:
    """Level 1 bid ask labels for dynamic update on price-axis.

    """
    max_value: float = '100.0 x 100 000.00'

    def __init__(
        self,
        chart: 'ChartPlotWidget',  # noqa
        digits: int = 2,
        size_digits: int = 0,
        font_size_inches: float = _down_2_font_inches_we_like,
    ) -> None:

        self.chart = chart

        self.bid_label = L1Label(
            chart=chart,
            parent=chart.getAxis('right'),
            # TODO: pass this from symbol data
            digits=digits,
            opacity=1,
            font_size_inches=font_size_inches,
            bg_color='papas_special',
            fg_color='bracket',
            orient_v='bottom',
        )
        self.bid_label.size_digits = size_digits
        self.bid_label._size_br_from_str(self.max_value)

        self.ask_label = L1Label(
            chart=chart,
            parent=chart.getAxis('right'),
            # TODO: pass this from symbol data
            digits=digits,
            opacity=1,
            font_size_inches=font_size_inches,
            bg_color='papas_special',
            fg_color='bracket',
            orient_v='top',
        )
        self.ask_label.size_digits = size_digits
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

    # size 4 font on 4k screen scaled down, so small-ish.
    font_size_inches: float = _down_2_font_inches_we_like,

    show_label: bool = True,

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
        font_size_inches=font_size_inches,
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

    if not show_label:
        label.hide()

    return line
