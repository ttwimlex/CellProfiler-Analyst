# -*- Encoding: utf-8 -*-
import csv
import os
import re
import wx
import wx.grid as  gridlib
import wx.lib.intctrl as intctrl
import logging
import numpy as np
from cpatool import CPATool
from properties import Properties
import dbconnect
from datamodel import DataModel
import imagetools
from UserDict import DictMixin

p = Properties.getInstance()
db = dbconnect.DBConnect.getInstance()

ABC = list('ABCDEFGHIJKLMNOPQRSTUVWXYZ')
ABC += [x+y for x in ABC for y in ABC] + [x+y+z for x in ABC for y in ABC for z in ABC]
ROW_LABEL_SIZE = 30

class odict(DictMixin):
    ''' Ordered dictionary '''
    def __init__(self):
        self._keys = []
        self._data = {}
        
    def __setitem__(self, key, value):
        if key not in self._data:
            self._keys.append(key)
        self._data[key] = value
        
    def __getitem__(self, key):
        return self._data[key]
    
    def __delitem__(self, key):
        del self._data[key]
        self._keys.remove(key)
        
    def keys(self):
        return list(self._keys)
    
    def copy(self):
        copyDict = odict()
        copyDict._data = self._data.copy()
        copyDict._keys = self._keys[:]
        return copyDict

    
class TableData(gridlib.PyGridTableBase):
    '''
    Interface connecting the table grid GUI to the underlying table data.
    '''
    def __init__(self):
        self._rows = self.GetNumberRows()
        self._cols = self.GetNumberCols()
        gridlib.PyGridTableBase.__init__(self)
    
    def set_sort_col(self, col_index, add=False):
        '''Sort rows by the column indicated indexed by col_index. If add is 
        True, the column will be added to the end of a list of sort-by columns.
        '''
        raise NotImplementedError
    
    def get_image_keys_at_row(self, row):
        '''returns a list of image keys at a given row index or None.
        '''
        raise NotImplementedError
    
    def get_object_keys_at_row(self, row):
        '''returns a list of object keys at a given row index or None.
        '''
        raise NotImplementedError
    
    def set_filter(self, filter):
        '''filter - a per-image filter to apply to the data.
        '''
        #XXX: how does this apply to per-well data?
        self.filter = filter
    
    def set_key_indices(self, key_indices):
        '''key_indices - the indices of the key columns for this table data.
              These columns, taken together, should be UNIQUE for every row.
        '''
        self.key_indices = key_indices
    
    def set_grouping(self, group_name):
        '''group_name - group name that specifies how the data is grouped
              relative to the per image table.
        '''
        self.grouping = group_name

    def set_row_interval(self, rmin, rmax):
        '''rmin, rmax - min and max row indices to display.
              Used for displaying pages.
              Use None to leave the bound open.
        '''
        raise NotImplementedError

    def ResetView(self, grid):
        """
        (Grid) -> Reset the grid view.   Call this to
        update the grid if rows and columns have been added or deleted
        """
        grid.BeginBatch()
        for current, new, delmsg, addmsg in [
            (self._rows, self.GetNumberRows(), 
             gridlib.GRIDTABLE_NOTIFY_ROWS_DELETED, 
             gridlib.GRIDTABLE_NOTIFY_ROWS_APPENDED),
            (self._cols, self.GetNumberCols(), 
             gridlib.GRIDTABLE_NOTIFY_COLS_DELETED, 
             gridlib.GRIDTABLE_NOTIFY_COLS_APPENDED),
        ]:
            if new < current:
                msg = gridlib.GridTableMessage(self,delmsg,new,current-new)
                grid.ProcessTableMessage(msg)
            elif new > current:
                msg = gridlib.GridTableMessage(self,addmsg,new-current)
                grid.ProcessTableMessage(msg)
                self.UpdateValues(grid)
        grid.EndBatch()
        self._rows = self.GetNumberRows()
        self._cols = self.GetNumberCols()
        # update the column rendering plugins
##        self._updateColAttrs(grid)
        # update the scrollbars and the displayed part of the grid
        grid.AdjustScrollbars()
        grid.ForceRefresh()


    def UpdateValues(self, grid):
        """Update all displayed values"""
        # This sends an event to the grid table to update all of the values
        msg = gridlib.GridTableMessage(self, gridlib.GRIDTABLE_REQUEST_VIEW_GET_VALUES)
        grid.ProcessTableMessage(msg)


# Data could be aggregated many ways... need to know which way so image keys and
# object keys can be returned faithfully
# XXX: implement get_object_keys_at_row
# XXX: Consider consuming this functionality into DBTable by automatically 
#      transforming all tables into DB temporary tables.
#      Tables could then be made permanent by saving to CSV or DB.
class PlainTable(TableData):
    '''
    Generic table interface for displaying tabular data, eg, from a csv file.
    If the image key column names exist in the column labels then the values 
    from these columns will be used to link the data to the images
    '''
    def __init__(self, grid, data, col_labels=None, key_indices=None, 
                 grouping=None):
        '''
        Arguments:
        grid -- parent grid
        data -- the table data as a 2D np object array
        col_labels -- text labels for each column
        key_indices -- indices of columns that constitute a unique key for the table
        grouping -- a group name that specifies how the data is grouped relative
                    to the per image table.
        '''
        if col_labels is None:
            col_labels = ABC[:data.shape[1]]
        
        assert len(col_labels) == data.shape[1], "Number of column labels does not match the number of columns in data."
        self.sortdir       =  1    # sort direction (1=descending, -1=descending)
        self.sortcols      =  []   # column indices being sorted (in order)
        self.grid          =  grid
        self.data          =  data
        self.ordered_data  =  self.data
        self.col_labels    =  np.array(col_labels)
        self.shown_columns =  np.arange(len(self.col_labels))
        self.row_order     =  np.arange(self.data.shape[0])
        self.col_order     =  np.arange(self.data.shape[1])
        self.key_indices   =  key_indices
        self.grouping      =  grouping
        TableData.__init__(self)
        
    def set_shown_columns(self, col_indices):
        '''sets which column should be shown from the db table
        
        col_indices -- the indices of the columns to show (all others will be 
                       hidden)
        '''
        self.shown_columns = self.col_order = col_indices
        self.ordered_data = self.data[self.row_order,:][:,self.col_order]
        
    def set_key_col_indices(self, indices):
        '''Sets the indices (starting at 0) of the key columns. These are needed
        to relate tables to each other.
        eg: to relate a unique (Table, Well, Replicate) to a unique image key.
        '''
        for i in indices: 
            assert 0 < i < len(sortcols), 'Key column index (%s) was outside the relm of possible indices (0-%d).'%(i, len(self.sortcols)-1)
        self.key_indices = indices
        
    def get_image_keys_at_row(self, row):
        '''Returns a list of image keys at the given row or None if the column 
        names can't be found in col_labels
        '''
        if self.key_indices is None or self.grouping is None:
            return None
        else:
            if self.grouping.lower() == 'image':            
                return [tuple(self.ordered_data[row, self.key_indices])]
            else:
                dm = DataModel.getInstance()
                return dm.GetImagesInGroup(self.grouping, self.get_row_key(row))
        
    def get_object_keys_at_row(self, row):
        '''Returns a list of object keys at the given row or None if the column
        names can't be found in col_labels
        '''
        if self.key_indices is None or self.grouping is None:
            return None
        else:
            return self.ordered_data[row, self.key_indices]
        
    def get_row_key(self, row):
        '''Returns the key column values at the given row.
        '''
        if self.key_indices is None:
            return None
        else:
            return tuple(self.ordered_data[row, self.key_indices])
    
    def GetNumberRows(self):
        return self.ordered_data.shape[0]

    def GetNumberCols(self):
        return self.ordered_data.shape[1]

    def GetColLabelValueWithoutDecoration(self, col_index):
        '''returns the column label at a given index (without ^,v decoration)
        Note: this does not return hidden column labels
        '''
        return self.col_labels[self.shown_columns][col_index]
    
    def GetColLabelValue(self, col_index):
        '''returns the column label at a given index (for display)
        '''
        col = self.col_labels[self.shown_columns][col_index]
        if col_index in self.sortcols:
            return col+' [%s%s]'%(len(self.sortcols)>1 and self.sortcols.index(col_index) + 1 or '', 
                                 self.sortdir>0 and 'v' or '^') 
        return col

    def get_all_column_names(self):
        '''returns all (hidden and shown) column names in this table.
        '''
        return self.col_labels.tolist()

    def GetRowLabelValue(self, row):
        return '>'

    def IsEmptyCell(self, row, col):
        return False

    def GetValue(self, row, col):
        return self.ordered_data[row,col]

    def SetValue(self, row, col, value):
        logging.warn('You can not edit this table.')
        pass
    
    def GetColValues(self, col):
        return self.ordered_data[:,col]
    
    def set_sort_col(self, col_index, add=False):
        '''Set the column to sort this table by. If add is true, this column
        will be added to the end of the existing sort order (or removed from the
        sort order if it is already present.)
        '''
        if not add:
            if len(self.sortcols)>0 and col_index in self.sortcols:
                # If this column is already sorted, flip it
                self.row_order = self.row_order[::-1]
                self.sortdir = -self.sortdir
            else:
                self.sortdir = 1
                self.sortcols = [col_index]
                # If this column hasn't been sorted yet, then sort descending
                self.row_order = np.lexsort(self.data[:,self.col_order][:,self.sortcols[::-1]].T.tolist())
        else:
            if len(self.sortcols)>0 and col_index in self.sortcols:
                self.sortcols.remove(col_index)
            else:
                self.sortcols += [col_index]
            if self.sortcols==[]:
                # if all sort columns have been toggled off, reset row_order
                self.row_order = np.arange(self.data.shape[0])
            else:
                self.row_order = np.lexsort(self.data[:,self.sortcols[::-1]].T.tolist())
        self.ordered_data = self.data[self.row_order,:][:,self.col_order]
         
    def GetRowLabelValue(self, row):
        return '*'
    
class DBTable(TableData):
    '''
    Interface connecting the table grid GUI to the database tables.
    '''
    def __init__(self, table_name, rmin=None, rmax=None):
        self.set_table(table_name)
        self.filter = '' #'WHERE Image_Intensity_Actin_Total_intensity > 17000'
        self.set_row_interval(rmin, rmax)
        #XXX: should filter be defined at a higher level? Just UI?
        TableData.__init__(self)
        
    def set_table(self, table_name):
        self.table_name = table_name
        self.cache = odict()
        self.col_labels = np.array(db.GetColumnNames(self.table_name))
        self.shown_columns = np.arange(len(self.col_labels))
        self.order_by = [self.col_labels[0]]
        self.order_direction = 'ASC'
        self.key_indices = None
        if self.table_name == p.image_table:
            self.key_indices = [self.col_labels.tolist().index(v) for v in dbconnect.image_key_columns()]
        if self.table_name == p.object_table:
            self.key_indices = [self.col_labels.tolist().index(v) for v in dbconnect.object_key_columns()]
            
    def set_shown_columns(self, col_indices):
        '''sets which column should be shown from the db table
        
        col_indices -- the indices of the columns to show (all others will be 
                       hidden)
        '''
        self.shown_columns = col_indices
        self.cache.clear()
    
    def set_sort_col(self, col_index, add=False):
        col = self.col_labels[col_index]
        if add:
            if col in self.order_by:
                self.order_by.remove(col)
                if self.order_by == []:
                    self.order_by = [self.col_labels[0]]
            else:
                self.order_by += [col]
        else:
            if col in self.order_by:
                if self.order_direction == 'ASC':
                    self.order_direction = 'DESC'
                else:
                    self.order_direction = 'ASC'
            else:
                self.order_by = [col]
        self.cache.clear()
    
    def set_row_interval(self, rmin, rmax):
        self.cache.clear()
        if rmin == None: 
            rmin = 0
        if rmax == None: 
            rmax = self.get_total_number_of_rows()
        try:
            int(rmin)
            int(rmax)
        except:
            raise 'Invalid row interval, values must be positive numbers.'
        self.rmin = rmin
        self.rmax = rmax
        
    def get_row_key(self, row):
        cols = ','.join(self.col_labels[self.key_indices])
        key = db.execute('SELECT %s FROM %s %s ORDER BY %s LIMIT %s,%s'%
                          (cols, self.table_name, self.filter, 
                           ','.join([c+' '+self.order_direction for c in self.order_by]),
                           row, 1))[0]
        return key
    
    def get_image_keys_at_row(self, row):
        # XXX: needs to be updated to work for per_well data
        key = self.get_row_key(row)
        if self.table_name == p.image_table:
            return [key]
#            return [tuple([self.GetValue(row, col) for col in self.key_indices])]
        elif self.table_name == p.object_table:
            return [key[:-1]]
        else:
            raise NotImplementedError
    
    def get_total_number_of_rows(self):
        '''Returns the total number of rows in the database
        '''
        return int(db.execute('SELECT COUNT(*) FROM %s %s'%(self.table_name, self.filter))[0][0])
    
    def GetNumberRows(self):
        '''Returns the number of rows on the current page (between rmin,rmax)
        '''
        total = self.get_total_number_of_rows()
        if self.rmax and self.rmin:
            return min(self.rmax, total) - self.rmin + 1
        else:
            return total
    
    def GetNumberCols(self):
        return len(self.shown_columns)
    
    def GetColLabelValueWithoutDecoration(self, col_index):
        '''returns the column label at a given index (without ^,v decoration)
        Note: this does not return hidden column labels
        '''
        return self.col_labels[self.shown_columns][col_index]
    
    def GetColLabelValue(self, col_index):
        '''returns the column label at a given index (for display)
        '''
        col = self.col_labels[self.shown_columns][col_index]
        if col in self.order_by:
            return col+' [%s%s]'%(len(self.order_by)>1 and self.order_by.index(col) + 1 or '', 
                                 self.order_direction=='ASC' and 'v' or '^') 
        return col
    
    def get_all_column_names(self):
        '''returns all (hidden and shown) column names in this table.
        '''
        return db.GetColumnNames(self.table_name)

    def GetValue(self, row, col):
        row += self.rmin
        if not row in self.cache:
            print "query", row
            lo = max(row - 25, 0)
            hi = row + 25
            cols = ','.join(self.col_labels[self.shown_columns])
            vals = db.execute('SELECT %s FROM %s %s ORDER BY %s LIMIT %s,%s'%
                              (cols, self.table_name, self.filter, 
                               ','.join([c+' '+self.order_direction for c in self.order_by]),
                               lo, hi-lo), 
                              silent=False)
            self.cache.update((lo+i, v) for i,v in enumerate(vals))
            # if cache exceeds 1000 entries, clip to last 500
            if len(self.cache) > 5000:
                for key in self.cache.keys()[:-500]:
                    del self.cache[key]
        return self.cache[row][col]

    def SetValue(self, row, col, value):
        print 'SetValue(%d, %d, "%s") ignored.\n' % (row, col, value)
        
    def GetColValues(self, col):
        colname = self.col_labels[self.shown_columns][col]
        vals = db.execute('SELECT %s FROM %s %s ORDER BY %s'%
                          (colname, self.table_name, self.filter, 
                           ','.join([c+' '+self.order_direction for c in self.order_by])), 
                          silent=False)
        return np.array(vals).flatten()

    def GetRowLabelValue(self, row):
        return '*'

                
BTN_PREV = wx.NewId()
BTN_NEXT = wx.NewId()

class TableViewer(wx.Frame):
    '''
    Frame containing the data grid, and UI tools that operate on it.
    '''
    def __init__(self, parent, **kwargs):
        wx.Frame.__init__(self, parent, -1, size=(640,480), **kwargs)
##        CPATool.__init__(self)
        
        self.selected_cols = set([])
        
        # Toolbar
        '''
        tb = self.CreateToolBar(wx.TB_HORIZONTAL | wx.NO_BORDER | wx.TB_FLAT)
        tb.AddControl(wx.Button(tb, BTN_PREV, '<', size=(30,-1)))
        self.Bind(wx.EVT_BUTTON, self.OnPrev, id=BTN_PREV)
        self.row_min = intctrl.IntCtrl(tb, -1, 1, size=(60,-1), min=0)
        tb.AddControl(self.row_min)
        self.row_min.Bind(wx.EVT_TEXT, self.OnEditMin)
        self.row_max = intctrl.IntCtrl(tb, -1, 1000, size=(60,-1), min=0)
        tb.AddControl(self.row_max)
        self.row_max.Bind(wx.EVT_TEXT, self.OnEditMax)
        tb.AddControl(wx.Button(tb, BTN_NEXT, '>', size=(30,-1)))
        self.Bind(wx.EVT_BUTTON, self.OnNext, id=BTN_NEXT)
        tb.Realize()
        '''
        
        #
        # Create the menubar
        #
        self.SetMenuBar(wx.MenuBar())
        file_menu = wx.Menu()
        self.GetMenuBar().Append(file_menu, 'File')
        new_table_item = file_menu.Append(-1, 'New empty table\tCtrl+N')
        file_menu.AppendSeparator()
        load_csv_menu_item = file_menu.Append(-1, 'Load table from CSV\tCtrl+O')
        load_db_table_menu_item = file_menu.Append(-1, 'Load table from database\tCtrl+Shift+O')
        file_menu.AppendSeparator()
        save_csv_menu_item = file_menu.Append(-1, 'Save table to CSV\tCtrl+S')
        save_temp_table_menu_item = file_menu.Append(-1, 'Save table in database\tCtrl+Shift+S')
        view_menu = wx.Menu()
        self.GetMenuBar().Append(view_menu, 'View')
        column_width_menu = wx.Menu()
        show_hide_cols_item = view_menu.Append(-1, 'Show/Hide columns')
        view_menu.AppendMenu(-1, 'Column widths', column_width_menu)
        fixed_cols_menu_item = column_width_menu.Append(-1, 'Fixed width', kind=wx.ITEM_RADIO)
        fit_cols_menu_item = column_width_menu.Append(-1, 'Fit to table', kind=wx.ITEM_RADIO)
        
        self.CreateStatusBar()
        
        self.Bind(wx.EVT_MENU, self.on_new_table, new_table_item)
        self.Bind(wx.EVT_MENU, self.on_load_csv, load_csv_menu_item)
        self.Bind(wx.EVT_MENU, self.on_load_db_table, load_db_table_menu_item)
        self.Bind(wx.EVT_MENU, self.on_save_csv, save_csv_menu_item)
        self.Bind(wx.EVT_MENU, self.on_save_table_to_db, save_temp_table_menu_item)
        self.Bind(wx.EVT_MENU, self.on_show_hide_cols, show_hide_cols_item)
        self.Bind(wx.EVT_MENU, self.on_set_fixed_col_widths, fixed_cols_menu_item)
        self.Bind(wx.EVT_MENU, self.on_set_fitted_col_widths, fit_cols_menu_item)
        
        #
        # Create the grid
        #
        self.grid = gridlib.Grid(self)
        self.grid.SetRowLabelSize(ROW_LABEL_SIZE)
        self.grid.DisableCellEditControl()
        self.grid.EnableEditing(False)
        self.grid.SetCellHighlightPenWidth(0)
        # Help prevent spurious horizontal scrollbar
        self.grid.SetMargins(0-wx.SystemSettings_GetMetric(wx.SYS_VSCROLL_X),
                             0-wx.SystemSettings_GetMetric(wx.SYS_HSCROLL_Y))
        self.grid.SetRowLabelSize(ROW_LABEL_SIZE)

        self.grid.Bind(gridlib.EVT_GRID_CMD_LABEL_LEFT_CLICK, self.on_leftclick_label)
        gridlib.EVT_GRID_LABEL_LEFT_DCLICK(self.grid, self.on_dclick_label)
        gridlib.EVT_GRID_LABEL_RIGHT_CLICK(self.grid, self.on_rightclick_label)
        gridlib.EVT_GRID_SELECT_CELL(self.grid, self.on_select_cell)
        gridlib.EVT_GRID_RANGE_SELECT(self.grid, self.on_select_range)
        
    def on_select_cell(self, evt):
        evt.Skip()
    
    def on_select_range(self, evt):
        cols = set(range(evt.GetLeftCol(), evt.GetRightCol() + 1))
        # update the selection
        if evt.Selecting():
            self.selected_cols.update(cols)
        else:
            self.selected_cols.difference_update(cols)
        try:
            # try to summarize selected columns
            n, m = self.grid.Table.GetNumberRows(), len(self.selected_cols)
            block = np.empty((n, m))
            for k, j in enumerate(self.selected_cols):
                block[:,k] = self.grid.Table.GetColValues(j)
                self.SetStatusText(u"Sum: %f — Mean: %f — Std: %f" %
                                               (block.sum(), block.mean(), block.std()))
        except:
            self.SetStatusText("Cannot summarize columns.")

    def on_show_hide_cols(self, evt):
        column_names = self.grid.Table.get_all_column_names()
        dlg = wx.MultiChoiceDialog(self, 
                                   'Select the columns you would like to show',
                                   'Show/Hide Columns', column_names)
        dlg.SetSelections(self.grid.Table.shown_columns)
        if (dlg.ShowModal() == wx.ID_OK):
            selections = dlg.GetSelections()
            self.grid.Table.set_shown_columns(selections)
            self.grid.Table.ResetView(self.grid)
        
    def on_set_fixed_col_widths(self, evt):
        self.set_fixed_col_widths()
    def set_fixed_col_widths(self):
        self.Disconnect(-1, -1, wx.wxEVT_SIZE)
        self.grid.SetDefaultColSize(gridlib.GRID_DEFAULT_COL_WIDTH, True)
        self.Refresh()
    
    def on_set_fitted_col_widths(self, evt):
        self.set_fitted_col_widths()
    def set_fitted_col_widths(self):
        wx.EVT_SIZE(self, self.on_size)
        self.RescaleGrid()

    def table_from_array(self, data, col_labels=None, grouping=None, key_indices=None):
        '''Populates the grid with the given data.
        data -- 2d array of data
        col_labels -- labels for each column
        grouping -- group name for linking to images
        key_indices -- indices of the key columns
        '''
        table_base = PlainTable(self, data, col_labels, key_indices, grouping)
        self.grid.SetTable(table_base, True)
        self.grid.SetSelectionMode(self.grid.wxGridSelectColumns)

    def on_new_table(self, evt=None):
        '''Prompts user to for table dimensions and creates the table.
        '''
        user_is_stupid = True
        while user_is_stupid:
            dlg = wx.TextEntryDialog(
                self, 'How many columns?', 'How many columns?', '10')
            if dlg.ShowModal() == wx.ID_OK:
                try:
                    cols = int(dlg.GetValue())
                    if 1 <= cols <= 1000: user_is_stupid = False
                    else: raise 'You must enter a value between 1 and 1000'
                except:
                    raise 'You must enter a value between 1 and 1000'
            else:
                return
        user_is_stupid = True
        while user_is_stupid:
            dlg = wx.TextEntryDialog(
                self, 'How many rows?', 'How many rows?', '1000')
            if dlg.ShowModal() == wx.ID_OK:
                try:
                    rows = int(dlg.GetValue())
                    if 1 <= rows <= 100000: user_is_stupid = False
                    else: raise 'You must enter a value between 1 and 100000'
                except:
                    raise 'You must enter a value between 1 and 100000'
            else:
                return
        pos = (self.Position[0]+10, self.Position[1]+10)
        frame = TableViewer(self.Parent, pos=pos)
        frame.Show(True)
        frame.new_blank_table(rows, cols)
        frame.SetTitle('New_Table')
        self.grid.SetSelectionMode(self.grid.wxGridSelectColumns)
        
    def new_blank_table(self, rows, cols):
        data = np.array([''] * (rows * cols)).reshape((rows, cols))
        table_base = PlainTable(self, data)
        self.grid.SetTable(table_base, True)
        self.RescaleGrid()
        self.grid.SetSelectionMode(self.grid.wxGridSelectColumns)
        
    def on_load_db_table(self, evt=None):
        try:
            user_tables = wx.GetApp().user_tables
        except AttributeError:
            # running outside of main UI
            wx.GetApp().user_tables = []
            user_tables = []
        dlg = wx.SingleChoiceDialog(self, 
                'Select a table to load from the database',
                'Load table from database',
                [p.image_table, p.object_table] + user_tables, 
                wx.CHOICEDLG_STYLE)

        if dlg.ShowModal() == wx.ID_OK:
            table_name = dlg.GetStringSelection()
            pos = (self.Position[0]+10, self.Position[1]+10)
            frame = TableViewer(self.Parent, pos=pos)
            frame.Show(True)
            frame.load_db_table(table_name)

    def load_db_table(self, tablename):
        '''Populates the grid with the data found in a given table.
        '''
        table_base = DBTable(tablename)
        self.grid.SetTable(table_base, True)
        self.SetTitle(tablename)
        self.RescaleGrid()
        self.grid.SetSelectionMode(self.grid.wxGridSelectColumns)

    def on_load_csv(self, evt=None):
        '''Prompts the user for a csv file and loads it.
        '''
        dlg = wx.FileDialog(self, message='Choose a CSV file to load',
                            defaultDir=os.getcwd(),
                            wildcard='CSV files (*.csv)|*.csv',
                            style=wx.OPEN|wx.FD_CHANGE_DIR)
        if dlg.ShowModal() == wx.ID_OK:
            filename = dlg.GetPath()
            pos = (self.Position[0]+10, self.Position[1]+10)
            frame = TableViewer(self.Parent, pos=pos)
            frame.Show(True)
            frame.load_csv(filename)
            
    def load_csv(self, filename):
        '''Populates the grid with the the data in a CSV file.
        filename -- the path to a CSV file to load
        '''
        #
        # XXX: try using linecache so we don't need to load the whole file.
        #
        
        # infer types
        r = csv.reader(open(filename))
        dtable = dbconnect.get_data_table_from_csv_reader(r)
        first_row_types = db.InferColTypesFromData([dtable[0]], len(dtable[0]))
        coltypes = db.InferColTypesFromData(dtable[1:], len(dtable[0]))
        has_header_row = False
        if (not all([a == b for a, b in zip(first_row_types, coltypes)]) and 
            all([a.startswith('VARCHAR') for a in first_row_types]) and
            not all([b.startswith('VARCHAR') for b in coltypes])):
            has_header_row = True
        for i in range(len(coltypes)):
            if coltypes[i] == 'INT': coltypes[i] = int
            elif coltypes[i] == 'FLOAT': coltypes[i] = np.float32
            else: coltypes[i] = str
        # read data
        r = csv.reader(open(filename))
        if has_header_row:
            labels = r.next()
        else:
            labels = None
        data = []
        for row in r:
            data += [[coltypes[i](v) for i,v in enumerate(row)]]
        data = np.array(data, dtype=object)
        
        table_base = PlainTable(self, data, labels)
        self.grid.SetTable(table_base, True)
        self.grid.Refresh()
        self.SetTitle(filename)
        self.RescaleGrid()
        self.grid.SetSelectionMode(self.grid.wxGridSelectColumns)

    def on_leftclick_label(self, evt):
        if evt.Col >= 0:
            self.grid.Table.set_sort_col(evt.Col, add=evt.ShiftDown())
            self.grid.Refresh()
##        elif evt.Row >= 0:
##            self.grid.SetSelectionMode(self.grid.wxGridSelectRows)
##            self.grid.SelectRow(evt.Row)
##            self.on_rightclick_label(evt)

    def on_rightclick_label(self, evt):
        if evt.Row >= 0:
            keys = self.grid.Table.get_image_keys_at_row(evt.Row)
            if keys:
                self.show_popup_menu(keys, evt.GetPosition())

    def show_popup_menu(self, items, pos):
        self.popupItemById = {}
        menu = wx.Menu()
        menu.SetTitle('Show Image')
        for item in items:
            id = wx.NewId()
            self.popupItemById[id] = item
            menu.Append(id,str(item))
        menu.Bind(wx.EVT_MENU, self.on_select_image_from_popup)
        self.PopupMenu(menu, pos)

    def on_select_image_from_popup(self, evt):
        '''Handle selections from the popup menu.
        '''
        imkey = self.popupItemById[evt.GetId()]
        imagetools.ShowImage(imkey, p.image_channel_colors, parent=self)

    def on_dclick_label(self, evt):
        if evt.Row >= 0:
            imkeys = self.grid.Table.get_image_keys_at_row(evt.Row)
            if imkeys:
                #XXX: warn if there are a lot
                for imkey in imkeys:
                    imagetools.ShowImage(imkey, p.image_channel_colors,
                                         parent=self.Parent)

    def OnPrev(self, evt=None):
        rmax = int(self.row_max.GetValue())
        rmin = int(self.row_min.GetValue())
        diff = rmax - rmin + 1
        self.row_max.SetValue(max(rmax - diff, diff))
        self.row_min.SetValue(max(rmin - diff, 1))
        rmax = int(self.row_max.GetValue())
        rmin = int(self.row_min.GetValue())
        self.grid.Table.set_row_interval(rmin-1, rmax)
        self.grid.Table.ResetView(self.grid)


    def OnNext(self, evt=None):
        rmax = int(self.row_max.GetValue())
        rmin = int(self.row_min.GetValue())
        diff = rmax - rmin + 1
        self.row_max.SetValue(rmax + diff)
        self.row_min.SetValue(rmin + diff)
        rmax = int(self.row_max.GetValue())
        rmin = int(self.row_min.GetValue())
        self.grid.Table.set_row_interval(rmin-1, rmax)
        self.grid.Table.ResetView(self.grid)

    def OnEditMin(self, evt=None):
        rmax = int(self.row_max.GetValue())
        rmin = int(self.row_min.GetValue())
        self.grid.Table.set_row_interval(rmin-1, rmax)
        self.grid.Table.ResetView(self.grid)

    def OnEditMax(self, evt=None):
        rmax = int(self.row_max.GetValue())
        rmin = int(self.row_min.GetValue())
        self.grid.Table.set_row_interval(rmin-1, rmax)
        self.grid.Table.ResetView(self.grid)

    def on_save_csv(self, evt):
        defaultFileName = 'my_table.csv'
        saveDialog = wx.FileDialog(self, message="Save as:",
                                   defaultDir=os.getcwd(),
                                   defaultFile=defaultFileName,
                                   wildcard='csv|*',
                                   style=(wx.SAVE | wx.FD_OVERWRITE_PROMPT |
                                          wx.FD_CHANGE_DIR))
        if saveDialog.ShowModal() == wx.ID_OK:
            filename = saveDialog.GetPath()
            self.save_to_csv(filename)
            self.Title = filename
        saveDialog.Destroy()

    def save_to_csv(self, filename):
        f = open(filename, 'wb')
        w = csv.writer(f)
        w.writerow([self.grid.Table.GetColLabelValueWithoutDecoration(col) 
                    for col in range(self.grid.Table.GetNumberCols())])
        for row in range(self.grid.Table.GetNumberRows()):
            w.writerow([self.grid.Table.GetValue(row, col) 
                        for col in range(self.grid.Table.GetNumberCols())])
        f.close()
        logging.info('Table saved to %s'%filename)
##        self.file = filename

    def on_save_table_to_db(self, evt):
        valid = False
        while not valid:
            dlg = wx.TextEntryDialog(self, 'What do you want to name your table?', 
                            'Save table to database', self.Title)
            if dlg.ShowModal() != wx.ID_OK:
                return
            tablename = dlg.Value
            if not re.match('^[A-Za-z]\w*$', tablename):
                wx.MessageDialog(self, 'Table name must begin with a letter and may'
                                 'only contain letters, digits and "_"',
                                 'Invalid table name', wx.OK|wx.ICON_INFORMATION).ShowModal()
            elif db.table_exists(tablename):
                dlg = wx.MessageDialog(self, 
                    'The table "%s" already exists in the database. Overwrite it?'%(tablename),
                    'Table already exists', wx.YES_NO|wx.NO_DEFAULT|wx.ICON_WARNING)
                if dlg.ShowModal() == wx.ID_YES:
                    valid = True
            else:
                valid = True
                
        dlg = wx.SingleChoiceDialog(self, 'Do you want to be able to access\n'
                'this table after you close CPA?', 'Save table to database',
                ['Store for this session only.', 'Store permanantly.'], 
                wx.CHOICEDLG_STYLE)
        if dlg.ShowModal() != wx.ID_OK:
            return
        temporary = (dlg.GetSelection() == 0)
        
        colnames = [self.grid.Table.GetColLabelValueWithoutDecoration(col) 
                    for col in range(self.grid.Table.GetNumberCols())]
        data = [[self.grid.Table.GetValue(row, col) 
                for col in range(self.grid.Table.GetNumberCols())]
                for row in range(self.grid.Table.GetNumberRows())]
        db.CreateTempTableFromData(data, dbconnect.clean_up_colnames(colnames), 
                                   tablename, temporary=temporary)
        self.Title = tablename
        try:
            wx.GetApp().user_tables += [tablename]
            for plot in wx.GetApp().get_plots():
                if plot.tool_name == 'PlateViewer':
                    plot.AddTableChoice(tablename)
        except AttributeError:
            # running without main UI
            user_tables = wx.GetApp().user_tables = []

    def on_size(self, evt):
        if not self.grid:
            return
        # HACK CITY: Trying to fix spurious horizontal scrollbar
        adjustment = ROW_LABEL_SIZE
        if self.grid.GetScrollRange(wx.VERTICAL) > 0:
            adjustment = wx.SYS_VSCROLL_ARROW_X #+ 12
        cw = (evt.Size[0] - adjustment) / self.grid.Table.GetNumberCols()
        self.grid.SetDefaultColSize(cw, True)
        evt.Skip()
        
    def RescaleGrid(self):
        # Hack: resize window so the grid resizes to fit
        self.Size = self.Size+(1,1)
        self.Size = self.Size-(1,1)
        
    def save_settings(self):
        '''save_settings is called when saving a workspace to file.
        
        returns a dictionary mapping setting names to values encoded as strings
        '''
        pass
##        return {'table' : self.grid.Table.get_table(),
##                'sort_cols' : self.grid.Table.get_sort_cols(),
##                'row_interval' : self.grid.Table.get_row_interval(),
##                }
        
    def load_settings(self, settings):
        '''load_settings is called when loading a workspace from file.
        
        settings - a dictionary mapping setting names to values encoded as
                   strings.
        '''
        pass
        

if __name__ == '__main__':
    import sys
    app = wx.PySimpleApp()
    logging.basicConfig(level=logging.DEBUG,)
    if p.show_load_dialog():
        frame = TableViewer(None)
        frame.Show(True)
        frame.load_db_table(p.image_table)
    app.MainLoop()
