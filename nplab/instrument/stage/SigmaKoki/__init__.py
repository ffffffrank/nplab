# -*- coding: utf-8 -*-
"""
This is an interface module for instruments produced by Sigma Koki

__author__: Yago
"""

from nplab.utils.gui import QtWidgets, QtCore, uic, QtGui
from nplab.utils.thread_utils import locked_action
from nplab.instrument.stage import Stage, StageUI
from nplab.instrument.serial_instrument import SerialInstrument
from nplab.instrument.visa_instrument import VisaInstrument
import exceptions, time, sys, re, os
import nplab.ui
from nplab.utils.formatting import engineering_format
from functools import partial
from nplab.ui.ui_tools import UiTools



class GSC01(SerialInstrument, Stage):
    """
    Stage controller GSC-01
    """

    counts_per_degree = 400.
    axis_names = ('1', )
    metadata_property_names = ('position', )

    def __init__(self, address, **kwargs):

        self.port_settings = dict(baudrate=9600,
                                  bytesize=8,
                                  stopbits=1,
                                  parity='N',
                                  xonxoff=True,
                                  timeout=0.5,
                                  writeTimeout=0.5,
                                  rtscts=True
                                  )
        SerialInstrument.__init__(self, address)
        self.termination_character = '\r\n'
        Stage.__init__(self)

        if 'offsetOrigin' in kwargs:
            self.offsetOrigin(kwargs['offsetOrigin'])  # 20000)

        if 'home_on_start' in kwargs.keys():
            if kwargs['home_on_start']:
                self.MechanicalHome()

    def __del__(self):
        try:
            self.ser.close()
        except:
            self._logger.warn("Couldn't close GSC01")

    def wait(self):
        while self.getACK3() != 'R':
            time.sleep(0.1)
            # pass

    def write_cmd(self, command, read=True, wait=False):
        '''
        :param command: serial command to send to device
        :param read: if True, reads out standard 'OK' or 'NG' reply from the GSC-01
        :param fname: name of calling function, only useful when verbose
        :return:
        '''

        self.write(command)

        if read:
            reply = self.readline()
            self._logger.debug('[%s]: %s' % (command, reply))
        else:
            reply = self.readline()
            if reply != 'OK\n':
                self._logger.warn('%s replied %s' % (command, reply))

        if wait:
            self.wait()

        return reply

    def MechanicalHome(self):
        '''
        This command is used to detect the mechanical origin for a stage and set that position as the origin. The moving
        speed S: 500pps, F:5000ps, R:200mS. Running a stop command suspends the operation. Any other commands are not
        acceptable.
        :return:
        '''
        self.write_cmd('H:1', read=False, wait=True)

    def initializeOrigin(self):
        """
        Sets the origin to the current position.
        """
        self.write('R:1')

    def offsetOrigin(self, steps):
        """
        Sets and offset to the homing command, so that the origin is not beside the limit sensors
        Effective only for the homing operation in MINI system. Value is initialised to zero when turning power off.
        :param steps: offset steps (integer)
        :return:
        """

        self.write('S:N%d' % steps)

    @locked_action
    def move(self, pos, axis=None, relative=False, wait=True):
        counts = self.counts_per_degree * pos
        if relative:
            if not (-16777214 <= counts <= 16777214):
                raise exceptions.ValueError('stage1 must be between -16777214 and 16777214.')

            command = 'M:W'
            if counts >= 0:
                command += '+P%d' % counts
            else:
                command += '-P%d' % -counts
        else:
            command = 'A:W'
            if counts >= 0:
                command += '+P%d' % counts
            else:
                command += '-P%d' % -counts
        self.write_cmd(command, read=False)
        self._go()

        if wait:
            t0 = time.time()
            curpos = self.get_position()[0]
            while curpos != pos and time.time()-t0 < 10:
                curpos = self.get_position()[0]
                time.sleep(0.1)

    def get_position(self, axis=None):
        status = self.getStatus()
        counts = status.split(',')[0]
        position = int(counts)/self.counts_per_degree
        self._logger.debug('Status: %s. Counts: %s. Position returned %g' %(status, counts, position))
        return [position]

    def jog(self, direction, timeout=2):
        """
        Moves stage continuously at jogging speed for specified length of time.
        :param direction: either '+' or '-'
        :param timeout: in seconds
        :return:
        """

        self.write('J:1%s' % direction)
        t0 = time.time()
        self._go()
        while time.time() - t0 < timeout:
            time.sleep(0.1)
        self.decelerate()

    def _go(self):
        """
        Moves the stages. To be used internally.
        """
        self.write_cmd('G:', read=False)

    def decelerate(self):
        """
        Decelerates and stop the stages.
        """
        self.write('L:1')

    def stop(self):
        """
        Stops the stages immediately.
        """
        self.write('L:E')

    def setSpeed(self, minSpeed1, maxSpeed1, accelerationTime1):
        """
        Set minimum and maximum speeds and acceleration time.
        :param minSpeed1: between 100 and 20000, in steps of 100 [PPS]
        :param maxSpeed1: between 100 and 20000, in steps of 100 [PPS]
        :param accelerationTime1: between 0 and 1000 [ms]
        :return:
        """
        if not (100 <= minSpeed1 <= maxSpeed1 <= 20000):
            raise exceptions.ValueError('Must be 100 <= minSpeed1 <= maxSpeed1 <= 20000')

        if not (0 <= accelerationTime1 <= 1000):
            raise exceptions.ValueError('Must be 00 <= accelerationTime1 <= 1000.')

        self.write('D:1S%dF%dR%d' % (minSpeed1, maxSpeed1, accelerationTime1))

    def setJogSpeed(self, speed):
        """
        Set jog speed
        :param speed: between 100 and 20000, in steps of 100 [PPS]
        :return:
        """
        if 100 < speed < 20000:
            raise ValueError('Speed must be in 100-20000 range')

        self.write('S:J%d' % speed)

    def enableMotorExcitation(self, stage1=True):
        """
        Turn motor on/off
        :param stage1: True (on) or False (off)
        :return:
        """

        self.write('C:1%d' % stage1)

    def getStatus(self):
        """
        Gets the current status, consisting of position, command status, stop status, and motor readiness
        :return: position, ACK1, ACK2, ACK3
                ACK1:   X   Command Error
                        K   Command accepted normally
                ACK2:   L   Limit Sensor stop
                        K   Normal stop
                ACK3:   B   Busy
                        R   Ready
        """
        return self.write_cmd('Q:')

    def getACK3(self):
        """
        :return: 'R' if motor ready, 'B' if motor busy
        """
        self.write_cmd('!:', read=False)
        return self.readline()

    def getVersion(self):
        """
        Returns the ROM version
        """
        self.write('?:V', read=False)
        return self.readline()


class SHOT(VisaInstrument, Stage):
    axis_names = (1, 2)

    def __init__(self, address, **kwargs):

        self.port_settings = dict(baudrate=38400,
                                  bytesize=8,
                                  stopbits=1,
                                  parity='N',
                                  xonxoff=True,
                                  timeout=0.5,
                                  writeTimeout=0.5,
                                  rtscts=True
                                  )
        VisaInstrument.__init__(self, address)
        self.termination_character = '\r\n'
        Stage.__init__(self, unit="step")

    def _rom_version(self):
        """
        Request an internal ROM version from the controller.
        :return: str, ROM version
        """
        return self.query("?:V")

    def _go(self):
        """
        Moves the stages. To be used internally.
        """
        self._write_check('G:')

    @locked_action
    def _write_check(self, command, wait=False):
        self._logger.debug("Writing: %s" %command)
        self.write(command)
        self._logger.debug("Writing successful")
        reply = self.read()
        self._logger.debug("Read: %s" %reply)

        if reply == 'NG\n':
            self._logger.warn('%s replied %s' % (command, reply))
        else:
            if wait:
                self._wait()
            return reply

    def _wait(self):
        """
        Wait until controller is ready
        """
        while self.is_busy():
            time.sleep(0.1)

    def home(self, axis="W", direction="+"):
        """
        Detects the machine zero on the stage, and define the position as the home position
        :param axis: either 1, 2 or W (for both)
        :param direction: either + or -
        :return:
        """

        self._write_check("H:%s%s" % (axis, direction))

    def set_origin(self, axis="W"):
        """
        Sets the origin to the current position.
        :param axis: either 1, 2 or W (for both)
        """

        self._write_check('R:' + str(axis))

    def move(self, counts, axis=1, relative=False, wait=True):
        """

        :param counts: either an integer, or a tuple of two integers. Positive or negative
        :param axis: either 1, 2 or W (for both)
        :param relative:
        :param wait:
        :return:
        """

        if not hasattr(counts, '__iter__'):
            counts = (counts, )

        if relative:
            for count in counts:
                if not (-16777214 <= count <= 16777214):
                    raise exceptions.ValueError('stage1 must be between -16777214 and 16777214.')
            command = "M:"
        else:
            command = "A:"

        command += str(axis)
        for count in counts:
            if count >= 0:
                command += '+P%d' % count
            else:
                command += '-P%d' % -count

        self._write_check(command)
        self._go()

    def get_position(self, axis=None):
        status = self.status()
        counts = map(int, status.split(',')[:2])
        return counts

    def jog(self, axis="W", direction="+", timeout=2):
        """
        Moves stage continuously at jogging speed for specified length of time.

        :param axis: either 1, 2 or W (for both)
        :param direction: either + or -
        :param timeout: amount of seconds to jog for
        :return:
        """

        self._write_check("J:%s%s" % (axis, direction))

        t0 = time.time()
        self._go()
        while time.time() - t0 < timeout:
            time.sleep(0.1)
        self.decelerate()

    def decelerate(self, axis="W"):
        """
        Decelerates and stop the stages.
        :param axis: either 1, 2 or W (for both)
        """

        self._write_check('L:' + str(axis))

    def emergency_stop(self):
        """
        Stops the stages immediately.
        """
        self.write('L:E')

    def set_speed(self, axes, min_speed, max_speed, accel_time):
        """Changing the movement speed
        On turning ON the power, SHOT-102 will default a minimum speed (S), maximum speed (F), and
        acceleration/deceleration time (R), all set by switches 9 and 10 on DIP Switch 1 for each speed range.
        :param axis: either 1, 2 or W (for both)
        :param min_speed: integer or tuple of two integers
        :param max_speed: integer or tuple of two integers
        :param accel_time: integer or tuple of two integers
        :return:
        """

        if not (1 <= min_speed <= max_speed <= 20000):
            raise exceptions.ValueError('Must be 1 <= min_speed <= max_speed <= 20000')
        if not (0 <= accel_time <= 5000):
            raise exceptions.ValueError('Must be 0 <= accel_time <= 5000')
        if not hasattr(min_speed, "__iter__"):
            min_speed = tuple(min_speed)
        if not hasattr(max_speed, "__iter__"):
            max_speed = tuple(max_speed)
        if not hasattr(accel_time, "__iter__"):
            accel_time = tuple(accel_time)
        if axes == "W":
            if len(min_speed) != 2 or len(min_speed) != 2 or len(min_speed) != 2:
                raise exceptions.ValueError('You need to provide speeds and times for both axis')

        command = "D:%s" %axes
        for mn, mx, at in zip(min_speed, max_speed, accel_time):
            command += "S" + str(mn) + "F" + str(mx) + "R" + str(at)
        self._write_check(command)

    def on_off(self, axes, state):
        """
        Deenergizes (motor free) or Energizes (hold) the motor.
        Execute this command to move (rotate) stages manually. Once executed, the actual stage position does not
        coincide with the coordinate value being displayed. For proper positioning, perform zero return and make the
        stage position consistent with the coordinate value being displayed.
        :param axes: either 1, 2 or W (for both)
        :param state: 0 (off) or 1 (on)
        :return:
        """
        self._write_check("C:%s%s" % (axes, state))

    def status(self):
        """
        A command to check the validity of an immediately preceding command, and request a controller to return the
        state of stage operations, coordinates of axes, etc.
        :return: str coord_1, coord_2, ACK1, ACK2, ACK3
            coordinates are 10-digit data including sign (positive is space)
            ACK1:   X - command or parameter error
                    K - successful command
            ACK2:   L - axis 1 emergency stop
                    M - axis 2 emergency stop
                    W - both axis emergency stop
                    K - normal stop
            ACK3:   B - busy
                    R - ready
        """
        return self.query("Q:")

    def is_busy(self):
        """

        :return: bool
        """
        reply = self.query("!:")

        if reply == "B":
            return True
        else:
            return False

    def get_qt_ui(self):
        return HITUI(self)


class HIT(SerialInstrument, Stage):
    """
    Stage controller for the many-axis HIT controller.
    Implementing commands from C:/Users/USER/Drive_polariton/polariton_lab/Equipment_manuals/Sigma-Koki_stages/SHOT-102/SHOT-102.pdf
    """

    # TODO: add interpolation commands. They allow you to set a position in the plane of two axes and move towards it in a curved or straight path
    axis_names = map(str, range(8))

    def __init__(self, address, **kwargs):

        self.port_settings = dict(baudrate=38400,
                                  bytesize=8,
                                  stopbits=1,
                                  parity='N',
                                  xonxoff=True,
                                  timeout=0.5,
                                  writeTimeout=0.5,
                                  rtscts=True
                                  )
        SerialInstrument.__init__(self, address)
        self.termination_character = '\r\n'
        Stage.__init__(self, unit="step")
        # Stage.__init__(self)

        # if 'offsetOrigin' in kwargs:
        #     self.offsetOrigin(kwargs['offsetOrigin'])  # 20000)

        # if 'home_on_start' in kwargs.keys():
        #     if kwargs['home_on_start']:
        #         self.MechanicalHome()

    def move(self, counts, axes=None, relative=False):
        if axes is None:
            axes = range(len(self.axis_names))
        if not hasattr(counts, '__iter__'):
            counts = (counts, )
        if not hasattr(axes, '__iter__'):
            axes = (axes, )

        if relative:
            command = 'M' # + ',' * axis + str(counts)
        else:
            command = 'A' #+ ',' * axis + str(counts)

        self.multi_axis_cmd(command, axes, counts, wait=True)

    def get_position(self, axes=None):
        if axes is None:
            axes = range(len(self.axis_names))
        all_positions = self.query("Q:").split(",")
        positions = []
        for ax in axes:
            try:
                positions += [int(all_positions[ax])]
            except ValueError:
                positions += [None]
        return positions

    def status(self, axes=None):
        if axes is None:
            axes = range(len(self.axis_names))
        statuses = self.query("Q:S").split(",")
        status = []
        for ax in axes:
            status += [statuses[ax]]
        return status

    def is_busy(self, axes=None):
        if axes is None:
            axes = range(len(self.axis_names))
        statuses = self.query("!:").split(",")
        status = []
        for ax in axes:
            status += [bool(statuses[ax])]
        return status

    def wait(self):
        '''
        If any of the axes are in motion, wait.

        :return:
        '''
        while '1' in self.query('!:'):
            time.sleep(0.1)

    @locked_action
    def write_check(self, command, wait=False):
        self.write(command)

        reply = self.readline()[:-1]  # excluding the \n termination

        if reply == 'NG':
            self._logger.warn('%s replied %s' % (command, reply))
        else:
            if wait:
                self.wait()
            return reply

    def multi_axis_cmd(self, command, axes, parameters, wait=False):
        '''
        Adds error checking to the write function

        :param command: serial command to send to device
        :param wait (bool): if True, calls self.wait before returning
        :return:
        '''

        if not hasattr(axes, "__iter__"):
            axes = (axes,)
        if not hasattr(parameters, "__init__"):
            parameters = [parameters] * len(axes)

        argument_list = ['DUMMY']*8
        for ax, param in zip(axes, parameters):
            argument_list[ax] = str(param)
        argument_string = ','.join(argument_list)
        argument_string = argument_string.replace('DUMMY', '')
        command += ':' + argument_string
        self._logger.debug('Writing: %s' %command)

        self.write_check(command)

        # reply = self.readline()[:-1]  # excluding the \n termination
        #
        # if reply == 'NG':
        #     self._logger.warn('%s replied %s' % (command, reply))
        # else:
        #     if wait:
        #         self.wait()
        #     return reply

    def mechanical_home(self, axes):
        '''
        This command is used to detect the mechanical origin for a stage and set that position as the origin. The moving
        speed S: 500pps, F:5000ps, R:200mS. Running a stop command suspends the operation. Any other commands are not
        acceptable.

        :param axes: list of axes
        :return:
        '''

        self.multi_axis_cmd('H', axes, [1]*len(axes), wait=True)

    def set_home(self, axes):
        """
        Sets the origin to the current position.
        """
        # cmnd = [0] * 8
        # for ax in axes:
        #     cmnd[ax] = 1

        self.multi_axis_cmd('R', axes, [1]*len(axes))

    def jog(self, directions, axes, timeout=2):
        """
        Moves stage continuously at jogging speed for specified length of time.
        :param direction: either '+' or '-'
        :param timeout: in seconds
        :return:
        """
        if not hasattr(directions, '__iter__'):
            directions = (directions, )
        if not hasattr(axes, '__iter__'):
            axes = (axes,)
        self.multi_axis_cmd('J', axes, directions)
        t0 = time.time()
        while time.time() - t0 < timeout:
            time.sleep(0.1)
        self.decelerate(axes)

    def decelerate(self, axes):
        """
        Decelerates and stop the stages.
        """
        self.multi_axis_cmd('L', axes, 1)

    def stop_all_stages(self):
        """
        Stops the stages immediately.
        """
        self.write_check('L:E')

    def set_speed(self, axis, start_speed, max_speed, acceleration_time):
        """
        Set minimum and maximum speeds and acceleration time.
        :param minSpeed1: between 100 and 20000, in steps of 100 [PPS]
        :param maxSpeed1: between 100 and 20000, in steps of 100 [PPS]
        :param accelerationTime1: between 0 and 1000 [ms]
        :return:
        """

        if not (1 <= start_speed <= max_speed <= 999999999):
            raise exceptions.ValueError('Must be 1 <= start_speed <= max_speed <= 999999999')

        if not (1 <= acceleration_time <= 1000):
            raise exceptions.ValueError('Must be 00 <= acceleration_time <= 1000.')

        self.write_check('D:%d,%d,%d,%d' % (axis, start_speed, max_speed, acceleration_time))

    def on_off(self, axes, on_off=None):
        """
        Turn motor on/off
        :param stage1: True (on) or False (off)
        :return:
        """
        if on_off is None:
            on_off = [1]*len(axes)
        elif hasattr(axes, '__iter__'):
            if not hasattr(on_off, '__iter__'):
                on_off = [on_off] * len(axes)
            elif len(axes) != len(on_off):
                raise ValueError('Axes list and on_off list need to be the same length')

        self.multi_axis_cmd('C', axes, on_off)

    def get_qt_ui(self):
        return HITUI(self)


class HITUI(QtWidgets.QWidget, UiTools):
    def __init__(self, stage):
        super(HITUI, self).__init__()
        self.stage = stage
        self.create_axes_layout()
        self.relative_step = 100

    def update_positions(self):
        positions = self.stage.get_position()
        for idx, position in enumerate(positions):
            self.positions[idx].setText(str(position))

    def create_axes_layout(self):
        uic.loadUi(os.path.join(os.path.dirname(__file__), 'stage.ui'), self)
        self.update_pos_button.clicked.connect(self.update_positions)
        path = os.path.dirname(os.path.realpath(nplab.ui.__file__))
        icon_size = QtCore.QSize(12, 12)
        self.positions = []
        self.set_positions = []
        self.set_position_buttons = []
        for i, ax in enumerate(self.stage.axis_names):
            col = 4 * (i / 3)
            position = QtWidgets.QLineEdit('', self)
            position.setReadOnly(True)
            self.positions.append(position)
            set_position = QtWidgets.QLineEdit('0', self)
            set_position.setMinimumWidth(40)
            self.set_positions.append(set_position)
            set_position_button = QtWidgets.QPushButton('', self)
            set_position_button.setIcon(QtGui.QIcon(os.path.join(path, 'go.png')))
            set_position_button.setIconSize(icon_size)
            set_position_button.resize(icon_size)
            set_position_button.clicked.connect(partial(self.button_pressed, i))
            self.set_position_buttons.append(set_position_button)
            # for each stage axis add a label, a field for the current position,
            # a field to set a new position and a button to set a new position ..
            self.info_layout.addWidget(QtWidgets.QLabel(str(ax), self), i % 3, col)
            self.info_layout.addWidget(position, i % 3, col + 1)
            self.info_layout.addWidget(set_position, i % 3, col + 2)
            self.info_layout.addWidget(set_position_button, i % 3, col + 3)

            # if i % 3 == 0:
            #     group = QtWidgets.QGroupBox('axes {0}'.format(1 + (i / 3)), self)
            #     layout = QtWidgets.QGridLayout()
            #     layout.setSpacing(3)
            #     group.setLayout(layout)
            #     self.axes_layout.addWidget(group, 0, i / 3)
            #     zero_button = QtWidgets.QPushButton('', self)
            #     zero_button.setIcon(QtGui.QIcon(os.path.join(path, 'zero.png')))
            #     zero_button.setIconSize(icon_size)
            #     zero_button.resize(icon_size)
            #     n = len(self.stage.axis_names) - i if len(self.stage.axis_names) - i < 3 else 3
            #     axes_set = self.stage.axis_names[i:i + n]
            #     #                zero_button.clicked.connect(partial(self.zero_all_axes, axes_set))
            #     layout.addWidget(zero_button, 1, 1)

            # step_size_select = QtWidgets.QComboBox(self)
            # step_size_select.addItems(self.step_size_values.keys())
            # step_size_select.activated[str].connect(partial(self.on_activated, i))
            # step_str = engineering_format(default_step, self.stage.unit)
            # step_index = self.step_size_values.keys().index(step_str)
            # step_size_select.setCurrentIndex(step_index)
            # layout.addWidget(QtWidgets.QLabel(str(ax), self), i % 3, 5)
            # layout.addWidget(step_size_select, i % 3, 6)
            # if i % 3 == 0:
            #     layout.addItem(QtWidgets.QSpacerItem(12, 0), 0, 4)

            plus_button = QtWidgets.QPushButton('', self)
            plus_button.clicked.connect(partial(self.move_axis_relative, i, 1))
            minus_button = QtWidgets.QPushButton('', self)
            minus_button.clicked.connect(partial(self.move_axis_relative, i, -1))
            plus_button.setIcon(QtGui.QIcon(os.path.join(path, 'up.png')))
            minus_button.setIcon(QtGui.QIcon(os.path.join(path, 'down.png')))
            self.axes_layout.addWidget(QtWidgets.QLabel(str(ax), self), 0, i)
            self.axes_layout.addWidget(plus_button, 1, i)
            self.axes_layout.addWidget(minus_button, 2, i)

            # if i % 3 == 0:
            #     plus_button.setIcon(QtGui.QIcon(os.path.join(path, 'right.png')))
            #     minus_button.setIcon(QtGui.QIcon(os.path.join(path, 'left.png')))
            #     layout.addWidget(minus_button, 1, 0)
            #     layout.addWidget(plus_button, 1, 2)
            # elif i % 3 == 1:
            #     plus_button.setIcon(QtGui.QIcon(os.path.join(path, 'up.png')))
            #     minus_button.setIcon(QtGui.QIcon(os.path.join(path, 'down.png')))
            #     layout.addWidget(plus_button, 0, 1)
            #     layout.addWidget(minus_button, 2, 1)
            # elif i % 3 == 2:
            #     plus_button.setIcon(QtGui.QIcon(os.path.join(path, 'up.png')))
            #     minus_button.setIcon(QtGui.QIcon(os.path.join(path, 'down.png')))
            #     layout.addWidget(plus_button, 0, 3)
            #     layout.addWidget(minus_button, 2, 3)
            plus_button.setIconSize(icon_size)
            plus_button.resize(icon_size)
            minus_button.setIconSize(icon_size)
            minus_button.resize(icon_size)

    def button_pressed(self, ax_idx, *args, **kwargs):
        position = int(self.set_positions[ax_idx].text())
        self.stage.move(position, ax_idx)
        self.update_positions()

    def move_axis_relative(self, ax_idx, direction):
        self.stage.move(direction * self.relative_step, ax_idx, relative=True)
        self.update_positions()


class Acton(VisaInstrument):

    def __init__(self, address, **kwargs):

        self.port_settings = dict(baudrate=9600,
                                  bytesize=8,
                                  stopbits=1,
                                  parity='N',
                                  xonxoff=True,
                                  timeout=0.5,
                                  writeTimeout=0.5,
                                  rtscts=True
                                  )
        VisaInstrument.__init__(self, address)
        self.termination_character = '\r\n'
        # if 'offsetOrigin' in kwargs:
        #     self.offsetOrigin(kwargs['offsetOrigin'])  # 20000)

        # if 'home_on_start' in kwargs.keys():
        #     if kwargs['home_on_start']:
        #         self.MechanicalHome()


if __name__ == '__main__':
    # gsc01 = GSC01('COM14')
    # print 'Line: ', gsc01.readline()
    # gsc01.MechanicalHome()
    # print 'Line: ', gsc01.readline()
    # gsc01.move(0)
    # print 'Line: ', gsc01.readline()
    # print 'Status: ', gsc01.getStatus()
    # # print 'Line: ', gsc01.readline()
    # gsc01.move(10)   # 265140
    # print 'Status: ', gsc01.getStatus()
    # gsc01.getStatus()
    # gsc01.move(10)
    # gsc01.getStatus()
    # gsc01.move(0)
    # gsc01.getStatus()

    # print 'Status: ', gsc01.getStatus()
    # print 'ACK3: ', gsc01.getACK3()
    # print 'ACK3: ', gsc01.getACK3()
    # print 'ACK3: ', gsc01.getACK3()
    # print 'ACK3: ', gsc01.getACK3()
    # print gsc01.get_position()
    # gsc01.show_gui()

    # enc = sys.getfilesystemencoding()
    #
    # hit = HIT('COM10')
    # hit._logger.setLevel("DEBUG")
    # hit.show_gui()

    shot = SHOT("GPIB0::8::INSTR")
    shot._logger.setLevel("DEBUG")
    shot.show_gui()
    # print shot.query("?:")
    #     matches = list(re.findall("'(.+?)'", err[0]))
    #     for dec in ['cp932', 'euc_jp', 'euc_jis_2004', 'euc_jisx0213', 'iso2022_jp', 'iso2022_jp_1', 'iso2022_jp_2',
    #                 'iso2022_jp_2004', 'iso2022_jp_3', 'iso2022_jp_ext', 'shift_jis', 'shift_jis_2004',
    #                 'shift_jisx0213']:
    #         # print str(matches[0]).decode('cp932')
    #         print str(matches[1]).decode(dec)
    #     for match in matches:
    #         print 'WHY'
    #         match.decode('cp932')
    #
    # fuck = '\ x83t\x83@\x83C\x83\x8b\x96\xbc\x81A\x83f\x83B\x83\x8c\x83N\x83g\x83\x8a\x96\xbc\x81A\x82\xdc\x82\xbd\x82\xcd\x83{\x83\x8a\x83\x85\x81[\x83\x80 \x83\x89\x83x\x83\x8b\x82\xcc\x8d\\\x95\xb6\x82\xaa\x8a\xd4\x88\xe1\x82\xc1\x82\xc4\x82\xa2\x82\xdc\x82\xb7\x81B'
    # print fuck.decode('cp932')
    #     # print err[0].decode('cp932')
    #     # print type(err)
    #     # print dir(err)
    #     # print err.args
    #     # print err.errno.decode('cp932')
    #     # print (u"%s: %s" % (err.strerror, err.filename.decode(enc))).encode(enc)
