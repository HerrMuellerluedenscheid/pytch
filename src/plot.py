import sys
import numpy as num
import scipy.interpolate as interpolate
import math
import logging

from pytch.two_channel_tuner import cross_spectrum, Worker

from pytch.data import MicrophoneRecorder, getaudiodevices, sampling_rate_options
from pytch.gui_util import AutoScaler, Projection, mean_decimation, minmax_decimation
from pytch.gui_util import make_QPolygonF, _color_names, _colors, _pen_styles    # noqa
from pytch.util import Profiler, smooth


if False:
    from PyQt4 import QtCore as qc
    from PyQt4 import QtGui as qg
    from PyQt4.QtGui import QApplication, QWidget, QHBoxLayout, QLabel, QMenu
    from PyQt4.QtGui import QMainWindow, QVBoxLayout, QComboBox, QGridLayout
    from PyQt4.QtGui import QAction, QSlider, QPushButton, QDockWidget, QFrame
else:
    from PyQt5 import QtCore as qc
    from PyQt5 import QtGui as qg
    from PyQt5.QtWidgets import QApplication, QWidget, QHBoxLayout, QLabel
    from PyQt5.QtWidgets import QMainWindow, QVBoxLayout, QComboBox
    from PyQt5.QtWidgets import QAction, QSlider, QPushButton, QDockWidget
    from PyQt5.QtWidgets import QCheckBox, QSizePolicy, QFrame, QMenu
    from PyQt5.QtWidgets import QGridLayout, QSpacerItem, QDialog, QLineEdit


logger = logging.getLogger(__name__)

try:
    from pytch.gui_util_opengl import GLWidget
    __PlotSuperClass = GLWidget
except ImportError:
    logger.warn('no opengl support')

    class PlotWidgetBase(QWidget):

        def paintEvent(self, e):
            painter = qg.QPainter(self)

            self.do_draw(painter)

        def do_draw(self, painter):
            raise Exception('to be implemented in subclass')

    __PlotSuperClass = PlotWidgetBase


use_pyqtgraph = False
tfollow = 3.
fmax = 2000.


channel_to_color = ['blue', 'red', 'green', 'black']


class InterpolatedColormap:
    ''' Continuously interpolating colormap '''
    def __init__(self, name=''):
        self.name = name
        self.colors = num.array([
                _colors['red'], _colors['green'], _colors['blue']
            ])

        self.values = num.linspace(0, 255., len(self.colors))
        self.r_interp = interpolate.interp1d(self.values, self.colors.T[0])
        self.g_interp = interpolate.interp1d(self.values, self.colors.T[1])
        self.b_interp = interpolate.interp1d(self.values, self.colors.T[2])
        self.proj = Projection()
        self.proj.set_out_range(0, 255.)

    def update(self):
        # why did i pass this?
        pass

    def _map(self, val):
        ''' Interpolate RGB colormap for *val*
        val can be a 1D array.

        Values which are out of range are clipped.
        '''
        val = self.proj.clipped(val)
        return self.r_interp(val), self.g_interp(val), self.b_interp(val)

    def map(self, val):
        return self._map(val)

    def map_to_QColor(self, val):
        return qg.QColor(*self.map(val))

    def set_vlim(self, vmin, vmax):
        self.proj.set_in_range(vmin, vmax)
        self.update()

    def get_incremented_values(self, n=40):
        ''' has to be implemented by every subclass. Needed for plotting.'''
        mi, ma = self.proj.get_in_range()
        return num.linspace(mi, ma, n)

    def get_visualization(self, callback=None):
        '''get dict of values and colors for visualization.

        :param callback: method to retrieve colors from value range.
                        default: *map*
        '''
        vals = self.get_incremented_values()

        if callback:
            colors = [callback(v) for v in vals]
        else:
            colors = [self.map(v) for v in vals]

        return vals, colors

    def __call__(self, val):
        return self._map(val)


class Colormap(InterpolatedColormap):
    ''' Like Colormap but with discrete resolution and precalculated.
    Can return tabulated QColors. Faster than Colormap'''

    def __init__(self, name='', n=20):
        InterpolatedColormap.__init__(self, name=name)
        self.n = n
        self.update()

    def update(self):
        vals = self.get_incremented_values()
        self.colors_QPen = []
        self.colors_QColor = []
        self.colors_rgb = []
        for v in vals:
            rgb = self._map(v)
            self.colors_rgb.append(rgb)
            c = qg.QColor(*rgb)
            self.colors_QColor.append(c)
            self.colors_QPen.append(qg.QPen(c))

    def get_incremented_values(self):
        return num.linspace(*self.proj.xr, num=self.n+1)

    def get_index(self, val):
        return int(self.proj.clipped(val)/self.proj.ur[1] * self.n)

    def map(self, val):
        return self.colors_rgb[self.get_index(val)]

    def map_to_QColor(self, val):
        return self.colors_QColor[self.get_index(val)]

    def map_to_QPen(self, val):
        i = self.get_index(val)
        return self.colors_QPen[i]


class ColormapWidget(QWidget):
    def __init__(self, colormap, *args, **kwargs):
        QWidget.__init__(self, *args, **kwargs)
        self.colormap = colormap
        self.yproj = Projection()
        size_policy = QSizePolicy()
        size_policy.setHorizontalPolicy(QSizePolicy.Maximum)
        self.setSizePolicy(size_policy)

        self.update()

    def update(self):
        _, rgb = self.colormap.get_visualization()
        self.vals, self.colors = self.colormap.get_visualization(
            callback=self.colormap.map_to_QColor)
        self.yproj.set_in_range(num.min(self.vals), num.max(self.vals))

    def paintEvent(self, e):
        rect = self.rect()
        self.yproj.set_out_range(rect.top(), rect.bottom())

        yvals = self.yproj(self.vals)
        painter = qg.QPainter(self)
        for i in range(len(self.vals)-1):
            patch = qc.QRect(qc.QPoint(rect.left(), yvals[i]),
                             qc.QPoint(rect.right(), yvals[i+1]))
            painter.save()
            #painter.fillRect(patch, qg.QBrush(self.colors[i]))
            painter.restore()

    def sizeHint(self):
        return qc.QSize(100, 500)


class GaugeWidget(__PlotSuperClass):
    def __init__(self, *args, **kwargs):
        super(GaugeWidget, self).__init__(*args, **kwargs)
        
        self._painter = None
        self.color = qg.QColor(0, 100, 100)
        self._val = 0
        self.set_title('')

        self.proj = Projection()
        self.proj.set_out_range(0., 2880)
        self.proj.set_in_range(0, 1500.)
        
        self.scaler = AutoScaler(
            no_exp_interval=(-3, 2), approx_ticks=7,
            snap=True
        )

        size_policy = QSizePolicy()
        size_policy.setHorizontalPolicy(QSizePolicy.Minimum)
        self.setSizePolicy(size_policy)
        self._f = -180./2880.
    
    #def resizeEvent(self, e):
    #    print(e)
    #    print(self._painter)
    #    if self._painter is not None:
    #        self.draw_deco(self._painter)
    #    #painter = qg.QPainter(self)
    #    #pen = qg.QPen(self.color, 20, qc.Qt.SolidLine)
    #    #painter.setPen(pen)
    #    super(GaugeWidget, self).resizeEvent(e)

    def do_draw(self, painter):
        ''' This is executed when self.repaint() is called'''
        painter.save()
        pen = qg.QPen(self.color, 20, qc.Qt.SolidLine)
        painter.setPen(pen)
        painter.drawArc(self.rect(), 2880., -self.proj.clipped(self._val))
        painter.restore()
        self._painter = painter

        self.draw_deco(painter)

    def draw_deco(self, painter):
        self.draw_ticks(painter)
        self.draw_title(painter)
    
    def draw_title(self, painter):
        painter.save()
        w, h = self.width(), self.height()
        painter.translate(w/2, h/2)
        painter.drawStaticText(1, 2, self.title)
        painter.restore()

    def draw_ticks(self, painter):
        # needs some performance polishing !!!
        painter.save()
        painter.translate(self.width()/2, self.height()/2)
        xmin, xmax, xinc = self.scaler.make_scale(self.proj.get_in_range())
        ticks = num.arange(xmin, xmax, xinc)
        ticks_proj = self.proj(ticks)
        # expensive. can be made cheaper. By creating list of lines first.
        for i, degree in enumerate(ticks_proj):
            painter.save()
            painter.rotate(degree * self._f)
            painter.drawLine(180, 0, 196, 0)
            painter.drawText(180, 0, str(ticks[-i]))
            painter.restore()
        
        painter.restore()

    def set_title(self, title):
        self.title = qg.QStaticText(title)

    def set_data(self, val):
        '''
        Call this method to update the arc
        '''
        self._val = val

    def sizeHint(self):
        return qc.QSize(500, 500)


class PlotWidget(__PlotSuperClass):
    ''' a plotwidget displays data (x, y coordinates). '''

    def __init__(self, *args, **kwargs):
        super(PlotWidget, self).__init__(*args, **kwargs)
        layout = QVBoxLayout()
        self.setLayout(layout)
        self.setContentsMargins(1, 1, 1, 1)

        self.track_start = None

        self.yscale = 1.
        self.tfollow = 0

        self.ymin = None
        self.ymax = None
        self.xmin = None
        self.xmax = None

        self._ymin = 0.
        self._ymax = 1.
        self._xmin = 0.
        self._xmax = 1.
        self._xinc = None

        self.left = 0.15
        self.right = 1.
        self.bottom = 0.1
        self.top = 0.1

        self.right_click_menu = QMenu(self)

        self.yticks = None
        self.xticks = None
        self.xzoom = 0.

        self.__show_grid = False
        #self.set_background_color('white')
        self.set_background_color('transparent')
        self.yscaler = AutoScaler(
            no_exp_interval=(-3, 2), approx_ticks=7,
            snap=True
        )

        self.xscaler = AutoScaler(
            no_exp_interval=(-3, 2), approx_ticks=7,
            snap=True
        )
        self.draw_fill = False
        self.draw_points = False
        select_scale = QAction('asdf', self.right_click_menu)
        self.set_pen()
        self.set_brush()
        self.addAction(select_scale)
        self._xvisible = num.empty(0)
        self._yvisible = num.empty(0)
        self.yproj = Projection()
        self.xproj = Projection()
        self.colormap = Colormap()

    def setup_annotation_boxes(self):
        ''' left and top boxes containing labels, dashes, marks, etc.'''
        w, h = self.wh
        l = self.left
        r = self.right
        t = self.bottom
        b = self.top

        tl = qc.QPoint((1.-b) * h, l * w)
        size = qc.QSize(w * (1. - (l + (1.-r))),
                        h * (1. - ((1.-t)+b)))
        self.x_annotation_rect = qc.QRect(tl, size)

    @property
    def wh(self):
        return self.width(), self.height()

    def test_refresh(self, npoints=100, ntimes=100):
        for i in range(ntimes):
            self.plot(num.random.random(npoints))
            self.repaint()

    def keyPressEvent(self, key_event):
        ''' react on keyboard keys when they are pressed.'''
        key_text = key_event.text()
        if key_text == 'q':
            self.close()

        elif key_text == 'f':
            self.showMaximized()

        QWidget.keyPressEvent(self, key_event)

    def set_brush(self, color='black'):
        self.brush = qg.QBrush(qg.QColor(*_colors[color]))

    def set_pen(self, color='black', line_width=1, pen_style='solid'):
        '''
        :param color: color name as string
        '''
        if pen_style == 'o':
            self.draw_points = True

        self.pen = qg.QPen(qg.QColor(*_colors[color]),
                           line_width, _pen_styles[pen_style])

    @property
    def show_grid(self):
        return self.__show_grid

    @show_grid.setter
    def show_grid(self, show):
        self.__show_grid = show

    def plot(self, xdata=None, ydata=None, ndecimate=0, envelope=False,
             style='solid', color='black', line_width=1, ignore_nan=False):
        ''' plot data

        :param *args:  ydata | xdata, ydata
        :param ignore_nan: skip values which are nan
        '''
        self.set_pen(color, line_width, style)
        if ydata is None:
            return

        if num.size(ydata) == 0:
            print('no data in array')
            return

        if xdata is None:
            xdata = num.arange(len(ydata))

        if ignore_nan:
            ydata = num.ma.masked_invalid(ydata)
            xdata = xdata[~ydata.mask]
            ydata = ydata[~ydata.mask]

        self.set_data(xdata, ydata, ndecimate)

    def fill_between(self, xdata, ydata1, ydata2, *args, **kwargs):
        x = num.hstack((xdata, xdata[::-1]))
        y = num.hstack((ydata1, ydata2[::-1]))
        self.draw_fill = True
        self.set_data(x, y)

    def set_data(self, xdata=None, ydata=None, ndecimate=0):
        #p = Profiler()
        if ndecimate != 0:
            #if True:
            #p.start()
            #self._xvisible = minmax_decimation(xdata, ndecimate)
            self._xvisible = xdata[::ndecimate]
            self._yvisible = minmax_decimation(ydata, ndecimate)
            #p.mark('finisehd mean')
            #self._yvisible = smooth(ydata, window_len=ndecimate*2)[::ndecimate]
            #index = num.arange(0, len(self._xvisible), ndecimate)
            #self._yvisible = self._yvisible[index]
            #self._xvisible = xdata[index]
            #p.mark('stop smooth')
            #print(p)
        else:
            self._xvisible = xdata
            self._yvisible = ydata
        
        if self._yvisible.size == 0:
            logger.warn('ydata array empty')
            return

        self.update_datalims()

    def plotlog(self, xdata=None, ydata=None, ndecimate=0, envelope=False,
                **style_kwargs):
        self.plot(xdata, num.log(ydata), ndecimate, envelope, **style_kwargs)

    def update_datalims(self):

        if self.ymin is None:
            self._ymin = num.min(self._yvisible)
        else:
            self._ymin = self.ymin
        if not self.ymax:
            self._ymax = num.max(self._yvisible)
        else:
            self._ymax = self.ymax
        
        self.colormap.set_vlim(self._ymin, self._ymax)

        if self.tfollow:
            self._xmin = num.max((num.max(self._xvisible) - self.tfollow, 0))
            self._xmax = num.max((num.max(self._xvisible), self.tfollow))
        else:
            if not self.xmin:
                self._xmin = num.min(self._xvisible)
            else:
                self._xmin = self.xmin

            if not self.xmax:
                self._xmax = num.max(self._xvisible)
            else:
                self._xmax = self.xmax

        w, h = self.wh

        mi, ma = self.xproj.get_out_range()
        drange = ma-mi
        xzoom = self.xzoom * drange/2.
        self.xproj.set_in_range(self._xmin - xzoom, self._xmax + xzoom)
        self.xproj.set_out_range(w * self.left, w * self.right)

        self.yproj.set_in_range(self._ymin, self._ymax)
        self.yproj.set_out_range(
            h*(1-self.bottom),
            h*self.top,)

    def set_background_color(self, color):
        '''
        :param color: color as string
        '''
        self.background_color = qg.QColor(*_colors[color])

    def set_xlim(self, xmin, xmax):
        ''' Set x data range. If unset scale to min|max of ydata range '''
        self.xmin = xmin
        self.xmax = xmax

    def set_ylim(self, ymin, ymax):
        ''' Set x data range. If unset scales to min|max of ydata range'''
        self.ymin = ymin
        self.ymax = ymax

    def do_draw(self, painter):
        ''' this is executed e.g. when self.repaint() is called. Draws the
        underlying data and scales the content to fit into the widget.'''

        if len(self._xvisible) == 0:
            return

        qpoints = make_QPolygonF(self.xproj(self._xvisible),
                                 self.yproj(self._yvisible))

        painter.save()
        painter.setRenderHint(qg.QPainter.Antialiasing)
        painter.fillRect(self.rect(), qg.QBrush(self.background_color))
        painter.setPen(self.pen)

        if not self.draw_fill and not self.draw_points:
            painter.drawPolyline(qpoints)

        elif self.draw_fill and not self.draw_points:
            painter.drawPolygon(qpoints)
            qpath = qg.QPainterPath()
            qpath.addPolygon(qpoints)
            painter.fillPath(qpath, self.brush)

        elif self.draw_points:
            painter.drawPoints(qpoints)

        else:
            raise Exception('dont know what to draw')

        painter.restore()
        self.draw_deco(painter)

    def draw_deco(self, painter):
        painter.save()
        self.draw_axes(painter)
        # self.draw_labels(painter)
        self.draw_y_ticks(painter)
        self.draw_x_ticks(painter)

        if self.show_grid:
            self.draw_grid_lines(painter)
        painter.restore()
    
    def draw_grid_lines(self, painter):
        ''' draw semi transparent grid lines'''
        w, h = self.wh
        ymin, ymax, yinc = self.yscaler.make_scale(
            (self._ymin, self._ymax)
        )
        ticks = num.arange(ymin, ymax, yinc)
        ticks_proj = self.yproj(ticks)

        lines = [qc.QLineF(w * self.left * 0.8, yval, w, yval)
                 for yval in ticks_proj]

        painter.save()
        painter.drawLines(lines)
        painter.restore()

    def draw_axes(self, painter):
        ''' draw x and y axis'''
        w, h = self.wh
        points = [qc.QPoint(w*self.left, h*(1.-self.bottom)),
                  qc.QPoint(w*self.left, h*(1.-self.top)),
                  qc.QPoint(w*self.right, h*(1.-self.top))]
        painter.drawPoints(qg.QPolygon(points))

    def set_xtick_increment(self, increment):
        self._xinc = increment

    def draw_x_ticks(self, painter):
        w, h = self.wh
        xmin, xmax, xinc = self.xscaler.make_scale((self._xmin, self._xmax))
        _xinc = self._xinc or xinc
        ticks = num.arange(xmin, xmax, _xinc)
        ticks_proj = self.xproj(ticks)
        tick_anchor = self.top*h
        lines = [qc.QLineF(xval, tick_anchor * 0.8, xval, tick_anchor)
                 for xval in ticks_proj]

        painter.drawLines(lines)
        #for i, xval in enumerate(ticks):
        #    painter.drawText(qc.QPointF(ticks_proj[i], tick_anchor), str(xval))

    def draw_y_ticks(self, painter):
        w, h = self.wh
        ymin, ymax, yinc = self.yscaler.make_scale(
            (self._ymin, self._ymax)
        )
        ticks = num.arange(ymin, ymax, yinc)
        ticks_proj = self.yproj(ticks)
        lines = [qc.QLineF(w * self.left * 0.8, yval, w*self.left, yval)
                 for yval in ticks_proj]
        painter.drawLines(lines)
        for i, yval in enumerate(ticks):
            painter.drawText(qc.QPointF(0, ticks_proj[i]), str(yval))

    def draw_labels(self, painter):
        self.setup_annotation_boxes()
        painter.drawText(self.x_annotation_rect, qc.Qt.AlignCenter, 'Time')

    def mousePressEvent(self, mouse_ev):
        self.test_refresh()
        point = self.mapFromGlobal(mouse_ev.globalPos())
        if mouse_ev.button() == qc.Qt.RightButton:
            self.right_click_menu.exec_(qg.QCursor.pos())
        elif mouse_ev.button() == qc.Qt.LeftButton:
            self.track_start = (point.x(), point.y())
            self.last_y = point.y()
            self._zoom_track = 0.

    def mouseReleaseEvent(self, mouse_event):
        self.track_start = None
        self._zoom_track = 0.

    def mouseMoveEvent(self, mouse_ev):
        point = self.mapFromGlobal(mouse_ev.globalPos())
        x0, y0 = self.track_start
        if self.track_start:
            self.dy = (y0 - point.y()) / float(self.height())
            self._zoom_track = self.dy

            self.last_y = point.y()

        self.xzoom = self._zoom_track


class PlotPitchWidget(PlotWidget):
    def __init__(self, *args, **kwargs):
        PlotWidget.__init__(self, *args, **kwargs)

    def fill_between(self, xdata1, ydata1, xdata2, ydata2, *args, **kwargs):
        '''
        plot only data points which are in both x arrays

        :param xdata1, xdata2: xdata arrays
        :param ydata1, ydata2: ydata arrays
        :param colors: either single color or rgb array
                of length(intersect(xdata1, xdata2))
        '''
        indxdata1 = num.in1d(xdata1, xdata2)
        indxdata2 = num.in1d(xdata2, xdata1)

        # this is usually done by *set_data*:
        self._xvisible = num.vstack((xdata1[indxdata1], xdata2[indxdata2]))
        self._yvisible = num.vstack((ydata1[indxdata1], ydata2[indxdata2]))

        self.update_datalims()

    def paintEvent(self, e):
        ''' this is executed e.g. when self.repaint() is called. Draws the
        underlying data and scales the content to fit into the widget.'''

        if len(self._xvisible) == 0:
            return
        # p = Profiler()
        # p.mark('start')

        lines = []
        pens = []
        dy = num.abs(self._yvisible[0] - self._yvisible[1])

        # SHOULD BE DONE OUTSIDE THIS SCOPE AND FIXED!
        x = self.xproj(self._xvisible)
        y = self.yproj(self._yvisible)
        # p.mark('start setup lines')

        for i in range(len(self._xvisible[0])):
            lines.append(qc.QLineF(x[0][i], y[0][i], x[1][i], y[1][i]))
            pens.append(self.colormap.map_to_QPen(dy[i]))
        # p.mark('start finished setup lines')

        painter = qg.QPainter(self)
        painter.setRenderHint(qg.QPainter.Antialiasing)
        #painter.fillRect(self.rect(), qg.QBrush(self.background_color))
        # p.mark('start draw lines')
        for iline, line in enumerate(lines):
            painter.save()
            painter.setPen(pens[iline])
            painter.drawLine(line)
            painter.restore()
        # p.mark('finished draw lines')
        self.draw_deco(painter)
        # print p

