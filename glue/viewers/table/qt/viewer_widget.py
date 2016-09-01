from __future__ import absolute_import, division, print_function

import os
import numpy as np

from qtpy.QtCore import Qt
from qtpy import QtCore, QtGui, QtWidgets
from qtpy import PYQT5
from matplotlib.colors import ColorConverter

from glue.config import viewer_tool
from glue.core.layer_artist import LayerArtistBase
from glue.core import message as msg
from glue.core import Data
from glue.utils import nonpartial
from glue.utils.qt import load_ui
from glue.viewers.common.qt.data_viewer import DataViewer
from glue.viewers.common.qt.toolbar import BasicToolbar
from glue.viewers.common.qt.tool import CheckableTool
from glue.core.subset import ElementSubsetState
from glue.core.edit_subset_mode import EditSubsetMode
from glue.core.state import lookup_class_with_patches
from glue.utils.colors import alpha_blend_colors
from glue.utils.qt import mpl_to_qt4_color
from glue.core.exceptions import IncompatibleAttribute

COLOR_CONVERTER = ColorConverter()


class DataTableModel(QtCore.QAbstractTableModel):

    def __init__(self, table_viewer):
        super(DataTableModel, self).__init__()
        if table_viewer.data.ndim != 1:
            raise ValueError("Can only use Table widget for 1D data")
        self._table_viewer = table_viewer
        self._data = table_viewer.data
        self.show_hidden = False
        self.order = np.arange(self._data.shape[0])

    def data_changed(self):
        top_left = self.index(0, 0)
        bottom_right = self.index(self.columnCount(), self.rowCount())
        self.dataChanged.emit(top_left, bottom_right)

    @property
    def columns(self):
        if self.show_hidden:
            return self._data.components
        else:
            return self._data.visible_components

    def columnCount(self, index=None):
        return len(self.columns)

    def rowCount(self, index=None):
        # Qt bug: Crashes on tables bigger than this
        return min(self._data.size, 71582788)

    def headerData(self, section, orientation, role):
        if role != Qt.DisplayRole:
            return None

        if orientation == Qt.Horizontal:
            return self.columns[section].label
        elif orientation == Qt.Vertical:
            return str(self.order[section])

    def data(self, index, role):

        if not index.isValid():
            return None

        if role == Qt.DisplayRole:

            c = self.columns[index.column()]
            idx = self.order[index.row()]
            comp = self._data.get_component(c)
            if comp.categorical:
                comp = comp.labels
            else:
                comp = comp.data
            if isinstance(comp[idx], bytes):
                return comp[idx].decode('ascii')
            else:
                return str(comp[idx])

        elif role == Qt.BackgroundRole:

            idx = self.order[index.row()]

            # Find all subsets that this index is part of
            colors = []
            for layer_artist in self._table_viewer.layers[::-1]:
                if layer_artist.visible:
                    subset = layer_artist.layer
                    try:
                        if subset.to_mask(view=slice(idx, idx + 1))[0]:
                            colors.append(subset.style.color)
                    except IncompatibleAttribute as exc:
                        layer_artist.disable_invalid_attributes(*exc.args)
                    else:
                        layer_artist.enabled = True

            # Blend the colors using alpha blending
            if len(colors) > 0:
                color = alpha_blend_colors(colors, additional_alpha=0.5)
                color = mpl_to_qt4_color(color)
                return QtGui.QBrush(color)

    def sort(self, column, ascending):
        c = self.columns[column]
        comp = self._data.get_component(c)
        if comp.categorical:
            self.order = np.argsort(comp.labels)
        else:
            self.order = np.argsort(comp.data)
        if ascending == Qt.DescendingOrder:
            self.order = self.order[::-1]
        self.layoutChanged.emit()


class TableLayerArtist(LayerArtistBase):
    def __init__(self, layer, table_viewer):
        self._table_viewer = table_viewer
        super(TableLayerArtist, self).__init__(layer)
    def redraw(self):
        self._table_viewer.model.data_changed()
    def update(self):
        pass
    def clear(self):
        pass


@viewer_tool
class RowSelectTool(CheckableTool):

    tool_id = 'table:rowselect'
    icon = 'glue_row_select'
    action_text = 'Select row(s)'
    tool_tip = ('Select rows by clicking on rows and pressing enter '
                'once the selection is ready to be applied')
    status_tip = 'CLICK to select, press ENTER to finalize selection'

    def __init__(self, viewer):
        super(RowSelectTool, self).__init__(viewer)
        self.deactivate()

    def activate(self):
        self.viewer.ui.table.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)

    def deactivate(self):
        self.viewer.ui.table.setSelectionMode(QtWidgets.QAbstractItemView.NoSelection)
        self.viewer.ui.table.clearSelection()


class TableWidget(DataViewer):

    LABEL = "Table Viewer"

    _toolbar_cls = BasicToolbar
    tools = ['table:rowselect']

    def __init__(self, session, parent=None, widget=None):

        super(TableWidget, self).__init__(session, parent)

        self.ui = load_ui('viewer_widget.ui',
                          directory=os.path.dirname(__file__))
        self.setCentralWidget(self.ui)

        hdr = self.ui.table.horizontalHeader()
        hdr.setStretchLastSection(True)

        if PYQT5:
            hdr.setSectionResizeMode(hdr.Interactive)
        else:
            hdr.setResizeMode(hdr.Interactive)

        hdr = self.ui.table.verticalHeader()

        if PYQT5:
            hdr.setSectionResizeMode(hdr.Interactive)
        else:
            hdr.setResizeMode(hdr.Interactive)

        self.model = None

    def keyPressEvent(self, event):
        if event.key() in [Qt.Key_Enter, Qt.Key_Return]:
            self.finalize_selection()
        super(TableWidget, self).keyPressEvent(event)

    def finalize_selection(self):
        model = self.ui.table.selectionModel()
        selected_rows = [self.model.order[x.row()] for x in model.selectedRows()]
        subset_state = ElementSubsetState(indices=selected_rows, data=self.data)
        mode = EditSubsetMode()
        mode.update(self._data, subset_state, focus_data=self.data)
        self.ui.table.clearSelection()

    def register_to_hub(self, hub):

        super(TableWidget, self).register_to_hub(hub)

        def dfilter(x):
            return x.sender.data is self.data

        hub.subscribe(self, msg.SubsetCreateMessage,
                      handler=nonpartial(self._refresh),
                      filter=dfilter)

        hub.subscribe(self, msg.SubsetUpdateMessage,
                      handler=nonpartial(self._refresh),
                      filter=dfilter)

        hub.subscribe(self, msg.SubsetDeleteMessage,
                      handler=nonpartial(self._refresh),
                      filter=dfilter)

        hub.subscribe(self, msg.DataUpdateMessage,
                      handler=nonpartial(self._refresh),
                      filter=dfilter)

    def _refresh(self):
        self._sync_layers()
        self.model.data_changed()

    def _sync_layers(self):

        # For now we don't show the data in the list because it always has to
        # be shown

        for layer_artist in self.layers:
            if layer_artist.layer not in self.data.subsets:
                self._layer_artist_container.remove(layer_artist)

        for subset in self.data.subsets:
            if subset not in self._layer_artist_container:
                self._layer_artist_container.append(TableLayerArtist(subset, self))

    def add_data(self, data):
        self.data = data
        self.setUpdatesEnabled(False)
        self.model = DataTableModel(self)
        self.ui.table.setModel(self.model)
        self.setUpdatesEnabled(True)
        self._sync_layers()
        return True

    def add_subset(self, subset):
        return True

    def unregister(self, hub):
        pass

    def closeEvent(self, event):
        """
        On close, Qt seems to scan through the entire model
        if the data set is big. To sidestep that,
        we swap out with a tiny data set before closing
        """
        d = Data(x=[0])
        self.ui.table.setModel(DataTableModel(d))
        event.accept()

    def restore_layers(self, rec, context):
        # For now this is a bit of a hack, we assume that all subsets saved
        # for this viewer are from dataset, so we just get Data object
        # then just sync the layers.
        for layer in rec:
            c = lookup_class_with_patches(layer.pop('_type'))
            props = dict((k, context.object(v)) for k, v in layer.items())
            layer = props['layer']
            self.add_data(layer.data)
            break
        self._sync_layers()
