from nplab.utils.gui import QtWidgets, uic, QtCore
from nplab.ui.ui_tools import UiTools
import nplab.datafile as df
from nplab.utils.log import create_logger, ColoredFormatter

import logging
import os
import inspect
import numpy as np
import pyqtgraph
import pyqtgraph.dockarea
import ctypes as ct
import datetime
import time


class GuiGenerator(QtWidgets.QMainWindow, UiTools):
    """A object for generating a main gui through stitching together multiple guis 
    by the generation of dock widgets, this allow the user to creaete a save a custom 
    gui without all of the hard work
    """

    def __init__(self, instrument_dict, parent=None, dock_settings_path=None,
                 scripts_path=None, working_directory=None):  #
        """Args:
            instrument_dict(dict) :     This is a dictionary containg the 
                                        instruments objects where the key is the 
                                        objects new name in the generated new Ipython 
                                        console
            dock_settings_path(str):    A path for loading a previous dock widget
                                        configuration
            script_path(str):           The path of any scripts the user may want to
                                        run using the drop down menu at the top
                                        of the gui
            working_directory(str):     A path to the requested working directory - 
                                        handy if you always wish to save data to 
                                        the same directorys
                                """
        super(GuiGenerator, self).__init__(parent)
        self._logger = create_logger('GeneratedGUI')
        self.instr_dict = instrument_dict
        if working_directory is None:
            self.working_directory = os.path.join(os.getcwd())
        else:
            self.working_directory = working_directory
        self.data_file = df.current(working_directory=working_directory)
        self.instr_dict['HDF5'] = self.data_file
        self.setDockNestingEnabled(1)

        uic.loadUi(os.path.join(os.path.dirname(__file__), 'guigenerator.ui'), self)

        self.allDocks = {}
        self.allWidgets = {}
        self.actions = dict(Views={}, Instruments={})

        self.dockwidgetArea = pyqtgraph.dockarea.DockArea()
        self.dockWidgetArea = self.replace_widget(self.verticalLayout, self.centralWidget(), self.dockwidgetArea)
        self.dockWidgetAllInstruments.setWidget(self.dockwidgetArea)
        self.dockWidgetAllInstruments.setTitleBarWidget(QtWidgets.QWidget())  # This trick makes the title bar disappear

        # Iterate over all the opened instruments. If the instrument has a GUI (i.e. if they have the get_qt_ui function
        # defined inside them), then create a pyqtgraph.Dock for it and add its widget to the Dock. Also prints out any
        # instruments that do not have GUIs
        self._logger.info('Opening all GUIs')

        for instr in self.instr_dict:
            self._open_one_gui(instr)

        self.terminalWindow = None
        self.menuTerminal()
        self._addActionViewMenu('Terminal')

        self.script_menu = None
        if scripts_path is not None:
            self.scripts_path = scripts_path
        else:
            self.scripts_path = 'scripts'
        self.makeScriptMenu()

        self.NightMode = 1

        # address of h5 file
        self.filename = df.current().filename

        #        self._tabifyAll()
        self._setupSignals()
        if dock_settings_path is not None:
            self.dock_settings_path = dock_settings_path
            self.menuLoadSettings()
        else:
            self.dock_settings_path = None
        self.showMaximized()

    def __getattribute__(self, name):  # All instruments log function and method calls at debugging level

        returned = QtCore.QObject.__getattribute__(self, name)
        if inspect.isfunction(returned) or inspect.ismethod(returned):
            codeline = inspect.getsourcelines(returned)[1]
            filename = inspect.getfile(returned)
            self._logger.debug('Called %s on line %g of %s' % (returned.__name__, codeline, filename))
        return returned

    def _open_one_gui(self, instrument_name):
        """A command for opening a single Instruemnt guiand creating a dock through acquiring the 
        get_qt_ui function for a single panel or if invidual control and preview widgets
        are possible then the get_control_widget and get_preview_widgets will be sed
        """
        if hasattr(self.instr_dict[instrument_name], 'get_control_widget') or hasattr(self.instr_dict[instrument_name],
                                                                                      'get_preview_widget'):
            if hasattr(self.instr_dict[instrument_name], 'get_control_widget'):
                self.allWidgets[instrument_name + ' controls'] = self.instr_dict[instrument_name].get_control_widget()
                self.allDocks[instrument_name + ' controls'] = pyqtgraph.dockarea.Dock(instrument_name + ' controls')
                self.dockwidgetArea.addDock(self.allDocks[instrument_name + ' controls'], 'left')
                self.allDocks[instrument_name + ' controls'].addWidget(self.allWidgets[instrument_name + ' controls'])
                self._addActionViewMenu(instrument_name + ' controls')
            if hasattr(self.instr_dict[instrument_name], 'get_preview_widget'):
                self.allWidgets[instrument_name + ' display'] = self.instr_dict[instrument_name].get_preview_widget()
                self.allDocks[instrument_name + ' display'] = pyqtgraph.dockarea.Dock(instrument_name + ' display')
                self.dockwidgetArea.addDock(self.allDocks[instrument_name + ' display'], 'left')
                self.allDocks[instrument_name + ' display'].addWidget(self.allWidgets[instrument_name + ' display'])
                self._addActionViewMenu(instrument_name + ' display')
        elif hasattr(self.instr_dict[instrument_name], 'get_qt_ui'):
            self.allWidgets[instrument_name] = self.instr_dict[instrument_name].get_qt_ui()
            self.allDocks[instrument_name] = pyqtgraph.dockarea.Dock(instrument_name)
            self.dockwidgetArea.addDock(self.allDocks[instrument_name], 'left')
            self.allDocks[instrument_name].addWidget(self.allWidgets[instrument_name])
            self._addActionViewMenu(instrument_name)
        else:
            self._logger.warn('%s does not have a get_qt_ui' % instrument_name)

    def _addActionViewMenu(self, instr):
        """Create the actions menu - such as enabled and disabling gui's on the fly """
        if instr not in self.actions['Views']:
            action = QtWidgets.QAction(instr, self)
            self.menuView.addAction(action)
            action.setCheckable(True)
            action.setChecked(True)
            action.triggered.connect(lambda: self._toggleView(instr))
            self.actions['Views'][instr] = action

    def _toggleView(self, instr):
        """A function for toggling a single gui. It does not work terribly well with docked widgets, likely due to some
        pyqtgraph issue"""
        if self.actions['Views'][instr].isChecked():
            self.allDocks[instr].show()
            # self.dockwidgetArea.addDock(self.allDocks[instr], 'left')
        else:
            self.allDocks[instr].hide()

    def _setupSignals(self):
        """Connect signals for the different general gui buttons/menu's """
        self.actionExit.triggered.connect(self.close)
        self.actionNightMode.triggered.connect(self.toggleNightMode)
        self.actionTerminal.triggered.connect(self.menuTerminal)
        # self.actionShowBrowser.triggered.connect(self.toggle_browser)
        self.actionNewExperiment.triggered.connect(self.menuNewExperiment)
        self.actionSaveExperiment.triggered.connect(self.menuSaveExperiment)
        self.actionSaveSettings.triggered.connect(self.menuSaveSettings)
        self.actionRecallSettings.triggered.connect(self.menuLoadSettings)
        # For some reason the following does not work if put in a loop
        actions = self.menuVerbose.actions()
        actions[0].triggered.connect(lambda: self.VerboseChanged(actions[0]))
        actions[1].triggered.connect(lambda: self.VerboseChanged(actions[1]))
        actions[2].triggered.connect(lambda: self.VerboseChanged(actions[2]))

    # def toggle_browser(self):
    #     """enable or disable the file browser """
    #     self.actions['Views']['HDF5'].toggle()
    #     self._toggleView('HDF5')

    def toggleNightMode(self):
        """A function to switch all the colors to night mode - handy when working in an optics lab """
        try:
            if self.actionNightMode.isChecked():
                import qdarkstyle
                self.setStyleSheet(qdarkstyle.load_stylesheet(pyside=False))
            else:
                self.setStyleSheet('')
        except Exception as e:
            print e
            print 'trying Qt 5'
            try:
                if self.actionNightMode.isChecked():
                    import qdarkstyle
                    self.setStyleSheet(qdarkstyle.load_stylesheet_pyqt5())
                else:
                    self.setStyleSheet('')
            except Exception as ee:
                print ee
                print 'Qt 5 style sheet failed'

    def menuSaveSettings(self):
        """A function for saving the current dock layout and settings to a numpy
        binary array file"""
        dock_state = self.dockWidgetArea.saveState()
        if self.dock_settings_path == None:
            import nplab.utils.gui
            from nplab.utils.gui import QtGui, QtWidgets
            #     app = nplab.utils.gui.get_qt_app()  # ensure Qt is running
            self.dock_settings_path = QtWidgets.QFileDialog.getSaveFileName(
                caption="Create new dock settings file",
                directory=self.working_directory,
                #            options=qtgui.QFileDialog.DontConfirmOverwrite,
            )[0]

        np.save(self.dock_settings_path, dock_state)

    def menuLoadSettings(self):
        """A function for loading the current dock layout and settings to a numpy
        binary array file"""
        if self.dock_settings_path == None:
            import nplab.utils.gui
            from nplab.utils.gui import QtGui, QtWidgets
            #          app = nplab.utils.gui.get_qt_app()  # ensure Qt is running
            self.dock_settings_path = QtWidgets.QFileDialog.getOpenFileName(
                caption="Select Existing Data File",
                directory=self.working_directory,
            )[0]
        try:
            loaded_state = np.load(self.dock_settings_path)
            loaded_state = loaded_state[()]
            self.dockWidgetArea.restoreState(loaded_state)
        except:
            self._logger.warn(
                'The dock_settings file does not exist! or it is for the wrong docks!')

    def menuNewExperiment(self):
        """A start new experiment button causing the gui to close and ask for a new file
            and reopen"""
        dock_state = self.dockWidgetArea.saveState()
        # self.toggle_browser()
        self.allDocks['HDF5'].close()
        self.instr_dict['HDF5'].close()
        del self.instr_dict['HDF5']
        del self.allDocks['HDF5']

        self.data_file = df.current()
        self.instr_dict['HDF5'] = self.data_file
        self._open_one_gui('HDF5')
        if os.environ["QT_API"] == "pyqt5":
            self.terminalWindow.push_vars(dict(HDF5=self.data_file))
        else:
            self.terminalWindow.push(dict(HDF5=self.data_file))
        # self._toggleView('HDF5')
        self.dockWidgetArea.restoreState(dock_state)

    def menuSaveExperiment(self):
        """push to data to hard drive """
        self.data_file.flush()

    def menuCloseExperiment(self):
        """Close the current data_file """
        self.data_file.close()
        self.allWidgets['HDF5'].treeWidget.model.refresh_tree()

    def menuTerminal(self):
        """ Create an ipython console for use within the experiment and push
        all the equipment to it with the requested names
        """
        from nplab.utils import terminal
        if self.terminalWindow is None:
            if os.environ["QT_API"] == "pyqt5":
                self.terminalWindow = terminal.QIPythonWidget()
                self.terminalWindow.push_vars({'gui': self, 'exper': self.instr_dict})
                self.terminalWindow.push_vars(self.instr_dict)
                self.terminalWindow.execute_command('import nplab.datafile as df')
                self.terminalWindow.execute_command('data_file = df.current()')
                self.terminalWindow.execute_command('')
                handle = logging.StreamHandler(self.terminalWindow.kernel_manager.kernel.stdout)
            else:
                self.terminalWindow = terminal.ipython()
                self.terminalWindow.push({'gui': self, 'exper': self.instr_dict})
                self.terminalWindow.push(self.instr_dict)
                self.terminalWindow.execute('import nplab.datafile as df')
                self.terminalWindow.execute('data_file = df.current()')
                self.terminalWindow.execute('')
                handle = logging.StreamHandler(self.terminalWindow.kernel.stdout)
            formatter = ColoredFormatter('[%(name)s] - %(levelname)s: %(message)s - %(asctime)s ', '%H:%M')
            handle.setFormatter(formatter)
            # self._logger.addHandler(handle)
            instr_logger = logging.getLogger('Instrument')
            # instr_logger.addHandler(handle)

            self.allDocks['Terminal'] = pyqtgraph.dockarea.Dock('Terminal')
            if os.environ["QT_API"] == "pyqt5":
                self.allWidgets['Terminal'] = self.terminalWindow
            else:
                self.allWidgets['Terminal'] = self.terminalWindow.control
            self.dockwidgetArea.addDock(self.allDocks['Terminal'], 'left')
            self.allDocks['Terminal'].addWidget(self.allWidgets['Terminal'])
        else:
            self.actions['Views']['Terminal'].toggle()
            self._toggleView('Terminal')

    '''Script menu'''

    def makeScriptMenu(self):
        """Generate a menu for running the scripts found in the scripts path locationlocation """
        from functools import partial

        if self.script_menu is None:
            script_menu = self.menuBar().addMenu('&Scripts')
        else:
            script_menu = self.script_menu

        menus = {self.scripts_path: script_menu}

        for dirpath, dirnames, filenames in os.walk(self.scripts_path):
            # print filenames
            current = menus[dirpath]
            for dn in dirnames:
                menus[os.path.join(dirpath, dn)] = current.addMenu(dn)
            for fn in filenames:
                if fn != '__init__.py':
                    menuitem = current.addAction(fn)
                    menuitem.triggered.connect(partial(self.menuScriptClicked, fn))

        script_menu.addSeparator()
        refreshScripts = script_menu.addAction('Refresh')
        refreshScripts.triggered.connect(self.refreshScriptMenu)
        self.script_menu = script_menu

    def refreshScriptMenu(self):
        """clear and recompile the scripts menu """
        self.script_menu.clear()
        self.makeScriptMenu()

    def menuScriptClicked(self, scriptname):
        """Runs the selected script """
        print "Clicked script %s" % scriptname
        if self.terminalWindow is None:
            self.menuTerminal()

        self.terminalWindow.run_script(scriptname)

    def VerboseChanged(self, action):
        """Automatically change the loggers 
        verbosity level across all instruments upon 
        request  """
        instr_logger = logging.getLogger('Instrument')
        if action.isChecked():
            self._logger.setLevel(action.text().upper())
            instr_logger.setLevel(action.text().upper())
            for action2 in self.menuVerbose.actions():
                if action2.text() != action.text():
                    action2.setChecked(False)
        else:
            self.menuVerbose.actions()[1].setChecked(True)
            instr_logger.setLevel('INFO')
            self._logger.setLevel('INFO')

    def closeEvent(self, event):
        """A quick are you sure you want to quit function """
        quit_msg = "Are you sure you want to exit the program?"
        print quit_msg
        try:
            if os.environ["QT_API"] == "pyqt5":
                reply = QtWidgets.QMessageBox.question(self, 'Message', quit_msg,
                                                       QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.Save | QtWidgets.QMessageBox.No)

            else:
                reply = QtWidgets.QMessageBox.question(self, 'Message', quit_msg,
                                                       QtWidgets.QMessageBox.Yes, QtWidgets.QMessageBox.Save,
                                                       QtWidgets.QMessageBox.No)

            if reply != QtWidgets.QMessageBox.No:
                if reply == QtWidgets.QMessageBox.Save:
                    self.menuSaveSettings()
                    # self.experiment.save_load_all_settings()
                #            self.experiment.isLive = 0

                #            if self.experiment.ExpFile is not None:
                #                self.experiment.ExpFile.flush()
                #                self.experiment.ExpFile.close()
                #            self.experiment.__del__()
                event.accept()
            else:
                event.ignore()
        except Exception as e:
            print e


def totuple(a):
    """
    Tries to turn 'a' (whatever it is) into a tuple, and if 'a' is an iterable, it tries to turn it into a tuple at
    all depth levels
    :param a:
    :return:
    """

    try:
        return tuple(totuple(i) for i in a)
    except TypeError:
        return a


def depth(lst):
    """
    Gets the depth of a list:
        depth([0,1,2,3,4]) is 1
        depth([[0,1],[2,3]]) is 2

    Args:
        lst: list or list of lists

    Returns:
        Depth of the list of lists provided
    """
    return isinstance(lst, list) and max(map(depth, lst)) + 1


class GeneralScan:  # (QtCore.QThread):

    def __init__(self, gui_generator, **kwargs):
        # self.prep_functions = prep_funcs(instrument_dictionary, gui)
        # self.measure_functions = measure_funcs(instrument_dictionary, gui)
        self.instr_dict = gui_generator.instr_dict
        self.pyqt_app = self.instr_dict['PyqtApp']
        self.gui = gui

        if 'series_name' in kwargs:
            self.series_name = kwargs['series_name']
        else:
            self.series_name = 'DefaultSeriesName'

        if 'email' in kwargs:
            assert type(kwargs['email']) == str
            self.email = True
            self.email_address = kwargs['email']
        else:
            self.email = False

        if 'file_name' in kwargs:
            self.instr_dict['HDF5'].close()
            self.instr_dict['HDF5'] = df.DataFile(kwargs['file_name'])
            self.instr_dict['HDF5'].make_current()
        if self.instr_dict["HDF5"] is None:
            self.gui.menuNewExperiment()

        self._logger = create_logger('Experiment.GeneralScan')
        if 'logging_level' in kwargs:
            self._logger.setLevel(kwargs['logging_level'])
        else:
            self._logger.setLevel('INFO')

        self.gui.abortScan = False

        self.iterable_variables = []
        self.measurements = []

    def make_ranges(self):
        """
        Using the given variable dictionaries, creates an list of tuples to be used by itertools in self._iter_func.
        Each tuple contains the values over which to iterate a particular variable. The order of the tuples corresponds
        to the order in which they are given in self.iterable_variables

        :return:
        """

        for measure in self.measurements:
            if 'depth' not in measure:
                measure['depth'] = len(self.iterable_variables)
            if 'save' not in measure:
                measure['save'] = True

        self.ranges = []
        for iterable_var in self.iterable_variables:
            # There are two options for giving values to a variable:
            #   - A 4-item list: ['linear'/'random', lower_value, upper_value, number_of_values]
            #   - An iterable containing the values you want
            first_value = iterable_var['values'][0]
            if type(first_value) == str:
                if first_value == 'linear':
                    appendable = np.linspace(*iterable_var['values'][1:])
                elif first_value == 'random':
                    appendable = np.random.uniform(*iterable_var['values'][1:])
                else:
                    raise ValueError("Unrecognised variable type")
            else:
                appendable = iterable_var['values']

            self.ranges.append(totuple(appendable))

    def is_viable(self):
        return True

    def scan_abort(self):
        """
        A scan can be paused or aborted at each iteration. This relies on the GUI having the attributes _abort_scan and
        _pause_scan and the function play_scan

        :return:
        """
        self._logger.warn('Aborting')
        if self.gui._abort_scan:
            toggle = ct.windll.user32.MessageBoxA(0, 'Continue (Y) or abort (N)?', '', 4)
            if toggle == 7:
                return True
            else:
                self.gui._abort_scan = False
                self.gui.play_scan()
                return False
        elif self.gui._pause_scan:
            while self.gui._pause_scan:
                time.sleep(0.05)
                self.pyqt_app.processEvents()
            return False
        else:
            return False

    def run(self):
        """Light wrapper of the _iter_func
        Ensures the scan gets initialised with the right ranges, and scan booleans. Also times how long the iteration
        takes, and also adds the option of emailing at the end of the scan

        :return:
        """

        # Ensures that the scan pause and abort options are False
        for button, value in zip(['Abort', 'Play'], [False, True]):
            getattr(self.gui, 'actionScan' + button).setChecked(not value)
            getattr(self.gui, 'actionScan' + button).trigger()

        self.make_ranges()

        t0 = time.clock()
        self._iter_func(0)
        t2 = time.clock()

        self._logger.info('%s finished at %s after %g min' % (self.series_name,
                                                              datetime.datetime.now().strftime('%H %M %S'),
                                                              (t2 - t0) / 60.))
        if self.email:
            email.send_email(self.email_address, "GeneralScan finished at %s, after %g minutes" % (
                datetime.datetime.now().strftime('%H %M %S'), '%H %M %S'), (t2 - t0) / 60.)

    def _iter_func(self, depthval):
        """
        Iterator function that creates nested 'for' loops for each given range, and then prepares and measures the
        experiment according to the user-given dictionary at each hierarchical level

        :param depthval: integer. Hierachical level inside the nested loops. 0 is the top level.
        :return:
        """
        if depthval == 0:
            self.foldername = [''] * (len(self.iterable_variables) + 2)
            self.foldername[0] = self.series_name

        if depthval < len(self.iterable_variables):
            iterable_var = self.iterable_variables[depthval]
            for value in self.ranges[depthval]:
                if self.scan_abort():
                    break

                if depthval == 0:
                    self._logger.info('Setting %s to %g' % (iterable_var, value))
                else:
                    self._logger.debug('Setting %s to %g' % (iterable_var, value))

                if 'name' not in iterable_var:
                    variable_name = iterable_var['instrument']
                    if 'function' in iterable_var:
                        variable_name += iterable_var['function']
                    if 'property' in iterable_var:
                        variable_name += iterable_var['property']
                else:
                    variable_name = iterable_var['name']

                self.foldername[depthval + 1] = '%s=%g' % (variable_name, value)
                folderstring = '/'.join(self.foldername[0:(2 + depthval)])
                if folderstring in self.instr_dict["HDF5"]:
                    self._logger.debug('%s already existed, creating another one' % folderstring)
                    index = 1
                    while folderstring + '-%i' % index in self.instr_dict["HDF5"]:
                        index += 1
                    self.foldername[depthval + 1] = '%s=%g-%i' % (variable_name, value, index)

                # Prepares the experiment according to the user-given dictionaries
                self._logger.debug("Calling _prepare: %s to %s" % (variable_name, value))
                self._prepare(value, iterable_var)

                # In most experiments, you would only want to measure at the lowest level of the nested loops. However,
                # this is here just in case you want to do a measurement beforehand (e.g. if you change the power, you
                # might want to measure it once, immediately after changing it)
                self._logger.debug("Calling _measure: ", depthval)
                self._measure(depthval)

                # Next level of the iterator
                self._iter_func(depthval + 1)
        else:
            self._logger.debug("Calling _measure: ", depthval)
            self._measure(depthval)

    def get_attributes(self, list_of_dictionaries):
        attributes = {}
        for dictionary in list_of_dictionaries:
            instr = self.instr_dict[dictionary['instrument']]
            attribute_name = dictionary['instrument'] + '.'
            if 'function' in dictionary:
                attribute_value = getattr(instr, dictionary['function'])
                attribute_name += dictionary['function']
            elif 'property' in dictionary:
                attribute_value = getattr(instr, dictionary['property'])
                attribute_name += dictionary['property']
            else:
                raise ValueError('Either a function or a property needs to be provided in the dictionary: ',
                                 variable_dictionary)
            attributes[attribute_name] = attribute_value

        self._logger.debug('Attributes: ', attributes)
        return attributes

    def _prepare(self, value, variable_dictionary):
        """
        Example variable_dictionary:
               dict(instrument='sample',
                    function="move", kwargs=dict(axes='x'),
                    values=np.linspace(1900000, 2000000, 11),
                    metadata=[dict(instrument='temp_gauge', property='temperature')])

        :param value:
        :param variable_dictionary:
        :return:
        """
        instr = self.instr_dict[variable_dictionary['instrument']]
        if 'function' in variable_dictionary:
            self._logger.debug('Preparing: %s %s %s' % (instr, variable_dictionary['function'], value))
            if 'kwargs' in variable_dictionary:
                kwargs = variable_dictionary['kwargs']
            else:
                kwargs = {}
            # print 'HEREHEREHERE: ', instr, variable_dictionary['function'], value, kwargs
            # time.sleep(1)
            getattr(instr, variable_dictionary['function'])(value, **kwargs)
            # time.sleep(1)
        elif 'property' in variable_dictionary:
            self._logger.debug('Preparing: %s %s %s' % (instr, variable_dictionary['property'], value))
            setattr(instr, variable_dictionary['property'], value)
        else:
            raise ValueError('Either a function or a property needs to be provided in the dictionary: ',
                             variable_dictionary)
        self.pyqt_app.processEvents()

        attributes = {}
        if 'metadata' in variable_dictionary:
            attributes = self.get_attributes(variable_dictionary['metadata'])
        self.pyqt_app.processEvents()

        self._logger.debug('Creating group: %s %s' % (self.foldername, attributes))
        # print self.foldername
        # print '/'.join(self.foldername)
        # print attributes
        self.instr_dict['HDF5'].create_group('/'.join(self.foldername), attributes)
        self.pyqt_app.processEvents()

    def _measure(self, depthval):
        """
        Iterate over all the measures, and perform the measurement for measures at correct depth, given by depthval

        :param depthval: int. hierarchical depth of the measurements to be carried out
        :return:
        """
        for measure_dictionary in self.measurements:
            if depthval == measure_dictionary['depth']:
                for prep in measure_dictionary['preparation']:
                    instr = self.instr_dict[prep['instrument']]
                    if 'function' in prep:
                        args = []
                        if 'args' in prep:
                            args = prep['args']
                        self._logger.debug('Measure preparing: %s %s %s' % (instr, prep['function'], args))
                        getattr(instr, prep['function'])(*args)
                    elif 'properties' in prep:
                        for prop, value in prep['properties'].items():
                            self._logger.debug('Preparing: %s %s %s' % (instr, prop, value))
                            setattr(instr, prop, value)
                    else:
                        raise ValueError('Either a function or a property needs to be provided in the dictionary: ',
                                         variable_dictionary)
                    self.pyqt_app.processEvents()

                for measurement in measure_dictionary['measurements']:
                    if 'name' in measurement:
                        measure_name = measurement['name']
                    else:
                        measure_name = measurement['instrument']
                    current_folder = list(self.foldername[0:(2 + depthval)])
                    current_folder[-1] = measure_name
                    dataset_name = '/'.join(current_folder)

                    instr = self.instr_dict[measurement['instrument']]
                    if measurement['instrument'] == 'streak':
                        try:
                            directory = self.instr_dict['HDF5'].dirname + '/' + dataset_name
                            if not os.path.exists(directory):
                                os.makedirs(directory)
                            directory += '/00001.tif'
                            self._logger.debug('Streak sequence: %s' % directory)
                            instr.start_sequence(directory, True)
                            self.pyqt_app.processEvents()
                        except Exception as e:
                            self._logger.warn('Streak failed at %s because: %s' % (line, e))
                    else:
                        attributes = {}
                        if 'metadata' in measurement:
                            attributes = self.get_attributes(measurement['metadata'])
                        self.pyqt_app.processEvents()

                        if 'function' in measurement:
                            self._logger.debug('Measuring: %s %s' % (instr, measurement['function']))
                            data = getattr(instr, measurement['function'])()
                        elif 'property' in measurement:
                            self._logger.debug('Measuring: %s %s' % (instr, measurement['property']))
                            data = getattr(instr, measurement['property'])
                        else:
                            raise ValueError('Either a function or a property needs to be provided in the dictionary: ',
                                             variable_dictionary)
                        self.pyqt_app.processEvents()

                        self._logger.info('Saving: %s %s' % (dataset_name, attributes))
                        self.instr_dict['HDF5'].create_dataset(dataset_name, data=data, attrs=attributes)
                        self.pyqt_app.processEvents()
                        self.instr_dict['HDF5'].flush()