# -*- coding: utf-8 -*-

from nplab.utils.gui import QtCore
from nplab.instrument.camera import CameraParameter
from nplab.utils.thread_utils import locked_action, background_action
from nplab.utils.log import create_logger
import nplab.datafile as df
from nplab.utils.notified_property import NotifiedProperty
import os
import platform
import time
from ctypes import *
import numpy as np

# TODO:
# Add a transpose checkbox for displaying (or maybe an option to rotate by 90, 180 or 270 degrees), without messing up the ROI and the axis
# Add unit transformation options for changing between real space, k-space, energy
#
# Can we use the pyqtgraph ROI that comes with the ImageDisplay instead of CrossHairs?

LOGGER = create_logger('Andor SDK')


def to_bits(integer, bits):
    """
    Returns a list of bits representing the integer in base 2. Can be used to parse over the capabilities
    :param integer:
    :param bits:
    :return:
    """
    assert integer < 2**bits
    return [1 if integer & (1 << (bits-1-n)) else 0 for n in range(bits)]


class AndorCapabilities(Structure):
    _fields_ = [("ulSize", c_ulong),
                ("ulAcqModes", c_ulong),
                ("ulReadModes", c_ulong),
                ("ulTriggerModes", c_ulong),
                ("ulCameraType", c_ulong),
                ("ulPixelMode", c_ulong),
                ("ulSetFunctions", c_ulong),
                ("ulGetFunctions", c_ulong),
                ("ulFeatures", c_ulong),
                ("ulPCICard", c_ulong),
                ("ulEMGainCapability", c_ulong),
                ("ulFTReadModes", c_ulong)]


class AndorWarning(Warning):
    def __init__(self, code, msg, reply):
        super(AndorWarning, self).__init__()
        self.error_code = code
        self.error_name = ERROR_CODE[code]

        self.msg = msg
        self.reply = reply

    def __str__(self):
        return self.error_name + '\n Error sent: ' + self.msg + '\n Error reply: ' + self.reply


class AndorParameter(CameraParameter):
    """Light wrapper of the CameraParameter that ensures the getting and setting of values takes care of possible
    multiple values"""

    def __init__(self, parameter_name, doc=None, read_back=True):
        super(AndorParameter, self).__init__(parameter_name, doc=doc, read_back=read_back)

    def fget(self, obj):
        value = super(AndorParameter, self).fget(obj)
        if (type(value) == tuple) and (len(value) == 1):
            return value[0]
        else:
            return value

    def fset(self, obj, value):
        if type(value) != tuple:
            value = (value,)
        super(AndorParameter, self).fset(obj, value)


class AndorBase:
    """
    Most of the code for this class is setting up a general way of reading and writing parameters, which are then set up
    from the parameters dictionary after class definition.

    The self.parameters dictionary contains all the information necessary to deal with the camera parameters. Each
    entry in the dictionary corresponds to a specific parameter and allows you to specify the Get and/or Set command
    name and datatype (from the .dll).

    Most parameters are straightforward, since the Andor dll either has inputs (for setting parameters) or outputs
    (for getting parameters). So you can just intuitively call Andor.GetParameter(name) or Andor.SetParameter(name, value)
    with name and value provided by the user.
    Some parameters, like VSSpeed, HSSpeed..., require inputs to get outputs, so the user must say, e.g.,
        Andor.GetParameter('VSSpeed', 0)
    Which does not return the current VSSpeed, but the VSSpeed (in us) of the setting 0.
    """

    def __init__(self):
        self._logger = LOGGER

        if platform.system() == 'Windows':
            if platform.architecture()[0] == '32bit':
                self.dll = windll.LoadLibrary(os.path.dirname(__file__) + "\\atmcd32d")
            elif platform.architecture()[0] == '64bit':
                self.dll = CDLL(os.path.dirname(__file__) + "\\atmcd64d")
            else:
                raise Exception("Cannot detect Windows architecture")
        elif platform.system() == "Linux":
            dllname = "usr/local/lib/libandor.so"
            self.dll = cdll.LoadLibrary(dllname)
        else:
            raise Exception("Cannot detect operating system for Andor")
        self.parameters = parameters
        self._parameters = dict()
        for key, value in parameters.items():
            if 'value' in value:
                self._parameters[key] = value['value']
            else:
                self._parameters[key] = None
        self.initialize()

    @background_action
    def __del__(self):
        """
        If the camera is a Classic or iCCD, you have to wait for the temperature to be higher than -20 before shutting
        down
        :return:
        """
        if self.Capabilities['CameraType'] in [3, 4]:
            if self.cooler:
                self.cooler = 0
            while self.CurrentTemperature < -20:
                print 'Waiting'
                time.sleep(1)
        self._logger.info('Shutting down')
        self._dll_wrapper('ShutDown')

    '''Base functions'''

    @locked_action
    def _dll_wrapper(self, funcname, inputs=(), outputs=(), reverse=False):
        """Handler for all the .dll calls of the Andor

        Parameters
        ----------
        funcname    Name of the dll function to be called
        inputs      Inputs to be handed in to the dll function
        outputs     Outputs to be expected from the dll
        reverse     Whether to have the inputs first or the outputs first when calling the dll

        Returns
        -------

        """
        dll_input = ()
        if reverse:
            for output in outputs:
                dll_input += (byref(output),)
            for inpt in inputs:
                dll_input += (inpt['type'](inpt['value']),)
        else:
            for inpt in inputs:
                dll_input += (inpt['type'](inpt['value']),)
            for output in outputs:
                dll_input += (byref(output),)
        error = getattr(self.dll, funcname)(*dll_input)
        self._error_handler(error, funcname, *(inputs + outputs))

        returnVals = ()
        for output in outputs:
            if hasattr(output, 'value'):
                returnVals += (output.value,)
            if isinstance(output, AndorCapabilities):
                dicc = {}
                for key, value in output._fields_:
                    dicc[key[2:]] = getattr(output, key)
                returnVals += (dicc,)
        if len(returnVals) == 1:
            return returnVals[0]
        else:
            return returnVals

    def _error_handler(self, error, funcname='', *args):
        self._logger.debug("[%s]: %s %s" % (funcname, ERROR_CODE[error], str(args)))
        if funcname == 'GetTemperature':
            return
        if error != 20002:
            raise AndorWarning(error, funcname, ERROR_CODE[error])

    def set_andor_parameter(self, param_loc, *inputs):
        """Parameter setter

        Using the information contained in the self.parameters dictionary, send a general parameter set command to the
        Andor. The command name, and number of inputs and their types are stored in the self.parameters

        Parameters
        ----------
        param_loc   dictionary key of self.parameters
        inputs      inputs required to set the particular parameter. Must be at least one

        Returns
        -------

        """
        if len(inputs) == 1 and type(inputs[0]) == tuple:
            if len(np.shape(inputs)) == 2:
                inputs = inputs[0]
            elif len(np.shape(inputs)) == 3:
                inputs = inputs[0][0]
        if 'not_supported' in self.parameters[param_loc] and self.parameters[param_loc]['not_supported']:
            return
        if 'Set' in self.parameters[param_loc]:
            func = self.parameters[param_loc]['Set']

            form_in = ()
            if 'Input_params' in func:
                for input_param in func['Input_params']:
                    form_in += ({'value': getattr(self, input_param[0]), 'type': input_param[1]},)
            for ii in range(len(inputs)):
                form_in += ({'value': inputs[ii], 'type': func['Inputs'][ii]},)
            try:
                self._dll_wrapper(func['cmdName'], inputs=form_in)

                if len(inputs) == 1:
                    self.parameters[param_loc]['value'] = inputs[0]
                    self._parameters[param_loc] = inputs[0]
                else:
                    self.parameters[param_loc]['value'] = inputs
                    self._parameters[param_loc] = inputs

                if 'Finally' in self.parameters[param_loc]:
                    self.get_andor_parameter(self.parameters[param_loc]['Finally'])
                    #       if 'GetAfterSet' in self.parameters[param_loc]:
                    #          self.GetParameter(param_loc, *inputs)
            except AndorWarning:
                if self.parameters[param_loc]['value'] is None:
                    self._logger.error('Not supported parameter and None value in the parameter dictionary')
                else:
                    self.parameters[param_loc]['not_supported'] = True
                    inputs = self.parameters[param_loc]['value']
                    if not isinstance(inputs, tuple):
                        inputs = (inputs, )

        if 'Get' not in self.parameters[param_loc].keys():
            if len(inputs) == 1:
                setattr(self, '_' + param_loc, inputs[0])
            else:
                setattr(self, '_' + param_loc, inputs)
            self.parameters[param_loc]['value'] = getattr(self, '_' + param_loc)
            self._parameters[param_loc] = getattr(self, '_' + param_loc)

    def get_andor_parameter(self, param_loc, *inputs):
        """Parameter getter

        Using the information contained in the self.parameters dictionary, send a general parameter get command to the
        Andor. The command name, and number of inputs and their types are stored in the self.parameters

        Parameters
        ----------
        param_loc   dictionary key of self.parameters
        inputs      optional inputs for getting the specific parameter

        Returns
        -------

        """
        if 'not_supported' in self.parameters[param_loc]:
            self._logger.debug('Ignoring get %s because it is not supported' % param_loc)
            self.parameters[param_loc]['value'] = getattr(self, '_' + param_loc)
            self._parameters[param_loc] = getattr(self, '_' + param_loc)
            return getattr(self, '_' + param_loc)
        if 'Get' in self.parameters[param_loc].keys():
            func = self.parameters[param_loc]['Get']

            form_out = ()
            if param_loc == 'Capabilities':
                form_out += (func['Outputs'][0],)
            else:
                for output in func['Outputs']:
                    form_out += (output(),)
            form_in = ()
            if 'Input_params' in func:
                for input_param in func['Input_params']:
                    form_in += ({'value': getattr(self, input_param[0]), 'type': input_param[1]},)
            for ii in range(len(inputs)):
                form_in += ({'value': inputs[ii], 'type': func['Inputs'][ii]},)
            if 'Iterator' not in func.keys():
                vals = self._dll_wrapper(func['cmdName'], inputs=form_in, outputs=form_out)
            else:
                vals = ()
                for i in range(getattr(self, func['Iterator'])):
                    form_in_iterator = form_in + ({'value': i, 'type': c_int},)
                    vals += (self._dll_wrapper(func['cmdName'], inputs=form_in_iterator, outputs=form_out),)
            # if len(vals) == 1:
            #     vals = vals[0]
            self.parameters[param_loc]['value'] = vals
            self._parameters[param_loc] = vals
            return vals
        elif 'Get_from_prop' in self.parameters[param_loc].keys() and hasattr(self, '_' + param_loc):
            vals = getattr(self, self.parameters[param_loc]['Get_from_prop'])[getattr(self, '_' + param_loc)]
            self.parameters[param_loc]['value'] = vals
            self._parameters[param_loc] = vals
            return vals
        elif 'Get_from_fixed_prop' in self.parameters[param_loc].keys():
            vals = getattr(self, self.parameters[param_loc]['Get_from_fixed_prop'])[0]
            self.parameters[param_loc]['value'] = vals
            self._parameters[param_loc] = vals
            return vals

        elif hasattr(self, '_' + param_loc):
            self.parameters[param_loc]['value'] = getattr(self, '_' + param_loc)
            self._parameters[param_loc] = getattr(self, '_' + param_loc)
            return getattr(self, '_' + param_loc)
        else:
            self._logger.info('The ' + param_loc + ' has not previously been set!')
            return None

    def get_andor_parameters(self):
        '''Gets all the parameters that can be gotten
        Returns:
            An up to date paramters dict containing only values and names
        '''
        param_dict = dict()
        for param in self.parameters:
            param_dict[param] = getattr(self, param)
        return

    def set_andor_parameters(self, parameter_dictionary):
        """Sets the values of the parameters listed within the dict Param_dict, It can take any number of parameters
        """
        assert isinstance(parameter_dictionary, dict)
        for name, value in parameter_dictionary.items():
            if not hasattr(self, name):
                self._logger.warn('The parameter ' + name + 'does not exist and therefore cannot be set')
                continue
            if value is None:
                self._logger.info('%s has not been set, as the value provided was "None" ' % name)
                continue

            if 'Get_from_prop' in self.parameters[name]:
                value = getattr(self, self.parameters[name]['Get_from_prop'])[
                    np.where(np.array(getattr(self, self.parameters[name]['Get_from_prop'])) == value)[0][0]]
            try:
                setattr(self, name, value)
            except Exception as e:
                self._logger.warn('Failed to set %s because %s' % (name, e))

    '''Used functions'''

    def abort(self):
        try:
            self._dll_wrapper('AbortAcquisition')
        except AndorWarning:
            pass

    def initialize(self):
        self._dll_wrapper('Initialize', outputs=(c_char(),))
        self.set_andor_parameter('ReadMode', 4)
        self.set_andor_parameter('AcquisitionMode', 1)
        self.set_andor_parameter('TriggerMode', 0)
        self.set_andor_parameter('Exposure', 0.01)
        detector_shape = self.get_andor_parameter('DetectorShape')
        self.set_andor_parameter('Image', 1, 1, 1, detector_shape[0], 1, detector_shape[1])
        self.set_andor_parameter('Shutter', 1, 0, 1, 1)
        self.set_andor_parameter('SetTemperature', -90)
        self.set_andor_parameter('CoolerMode', 0)
        self.set_andor_parameter('FanMode', 0)
        if self.Capabilities['EMGainCapability'] > 1:
            self.set_andor_parameter('OutAmp', 1)
        self.cooler = 1

    @locked_action
    def capture(self):
        """Capture function for Andor

        Wraps the three steps required for a camera acquisition: StartAcquisition, WaitForAcquisition and
        GetAcquiredData. The function also takes care of ensuring that the correct shape of array is passed to the
        GetAcquiredData call, according to the currently set parameters of the camera.

        Returns
        -------
        A numpy array containing the captured image(s)
        The number of images taken
        The shape of the images taken

        """
        self._dll_wrapper('StartAcquisition')
        self._dll_wrapper('WaitForAcquisition')
        self.wait_for_driver()

        if self._parameters['AcquisitionMode'] == 4:
            num_of_images = 1  # self.parameters['FastKinetics']['value'][1]
            image_shape = (self._parameters['FastKinetics'][-1], self._parameters['DetectorShape'][0])
        else:
            if self._parameters['AcquisitionMode'] == 1:
                num_of_images = 1
            elif self._parameters['AcquisitionMode'] == 2:
                num_of_images = 1
            elif self._parameters['AcquisitionMode'] == 3:
                num_of_images = self._parameters['NKin']
            else:
                raise NotImplementedError('Acquisition Mode %g' % self._parameters['AcquisitionMode'])

            if self._parameters['ReadMode'] == 0:
                if self._parameters['IsolatedCropMode'][0]:
                    image_shape = (
                        self._parameters['IsolatedCropMode'][2] / self._parameters['IsolatedCropMode'][
                            4],)
                else:
                    image_shape = (self._parameters['DetectorShape'][0] / self._parameters['FVBHBin'],)
            elif self._parameters['ReadMode'] == 3:
                image_shape = (self._parameters['DetectorShape'][0],)
            elif self._parameters['ReadMode'] == 4:
                if self._parameters['IsolatedCropMode'][0]:
                    image_shape = (
                        self._parameters['IsolatedCropMode'][1] / self._parameters['IsolatedCropMode'][
                            3],
                        self._parameters['IsolatedCropMode'][2] / self._parameters['IsolatedCropMode'][
                            4])
                else:
                    image_shape = (
                        (self._parameters['Image'][5] - self._parameters['Image'][4] + 1) /
                        self._parameters['Image'][1],
                        (self._parameters['Image'][3] - self._parameters['Image'][2] + 1) /
                        self._parameters['Image'][0],)
            else:
                raise NotImplementedError('Read Mode %g' % self._parameters['ReadMode'])

        dim = num_of_images * np.prod(image_shape)
        cimageArray = c_int * dim
        cimage = cimageArray()
        self._logger.debug('Getting AcquiredData for %i images with dimension %s' % (num_of_images, image_shape))
        try:
            self._dll_wrapper('GetAcquiredData', inputs=({'type': c_int, 'value': dim},), outputs=(cimage,),
                              reverse=True)
            imageArray = []
            for i in range(len(cimage)):
                imageArray.append(cimage[i])
        except RuntimeWarning as e:
            self._logger.warn('Had a RuntimeWarning: %s' % e)
            imageArray = []
            for i in range(len(cimage)):
                imageArray.append(0)

        return imageArray, num_of_images, image_shape

    def set_image(self, *params):
        """Set camera parameters for either the IsolatedCrop mode or Image mode

        Parameters
        ----------
        params  optional, inputs for either the IsolatedCrop mode or Image mode

        Returns
        -------

        """
        if self._parameters['IsolatedCropMode'][0]:
            if len(params) == 0:
                params += (self._parameters['IsolatedCropMode'])
            elif len(params) != 5:
                raise ValueError('Wrong number of parameters (need bool, cropheight, cropwidth, vbin, hbin')

            # Making sure we pass a valid set of parameters
            params = list(params)
            params[1] -= (params[1]) % params[3]
            params[2] -= (params[2]) % params[4]
            self.set_andor_parameter('IsolatedCropMode', *params)
        else:
            if len(params) == 0:
                params = self._parameters['Image']
            elif len(params) != 6:
                raise ValueError('Wrong number of parameters (need hbin, vbin, hstart, hend, vstart, vend')

            # Making sure we pass a valid set of parameters
            params = list(params)
            params[3] -= (params[3] - params[2] + 1) % params[0]
            params[5] -= (params[5] - params[4] + 1) % params[1]
            self.set_andor_parameter('Image', *params)

    @locked_action
    def set_fast_kinetics(self, n_rows=None):
        """Set the parameters for the Fast Kinetic mode

        Uses the already set parameters of exposure time, ReadMode, and binning as defaults to be passed to the Fast
        Kinetic parameter setter

        Parameters
        ----------
        n_rows

        Returns
        -------

        """

        if n_rows is None:
            n_rows = self._parameters['FastKinetics'][0]

        series_Length = int(self._parameters['DetectorShape'][1] / n_rows) - 1
        expT = self._parameters['AcquisitionTimings'][0]
        mode = self._parameters['ReadMode']
        hbin = self._parameters['Image'][0]
        vbin = self._parameters['Image'][1]
        offset = self._parameters['DetectorShape'][1] - n_rows

        self.set_andor_parameter('FastKinetics', n_rows, series_Length, expT, mode, hbin, vbin, offset)

    @property
    def status(self):
        error = self._dll_wrapper('GetStatus', outputs=(c_int(),))
        return ERROR_CODE[error]

    @locked_action
    def wait_for_driver(self):
        """
        This function is here because the dll.WaitForAcquisition does not work when in Accumulate mode

        Returns
        -------

        """
        status = c_int()
        self.dll.GetStatus(byref(status))
        while ERROR_CODE[status.value] == 'DRV_ACQUIRING':
            time.sleep(0.1)
            self.dll.GetStatus(byref(status))

    @property
    def cooler(self):
        return self._dll_wrapper('IsCoolerOn', outputs=(c_int(),))

    @cooler.setter
    def cooler(self, value):
        if value:
            self._dll_wrapper('CoolerON')
        else:
            self._dll_wrapper('CoolerOFF')

    def get_series_progress(self):
        acc = c_long()
        series = c_long()
        error = self.dll.GetAcquisitionProgress(byref(acc), byref(series))
        if ERROR_CODE[error] == "DRV_SUCCESS":
            return series.value
        else:
            return None

    def get_accumulation_progress(self):
        acc = c_long()
        series = c_long()
        error = self.dll.GetAcquisitionProgress(byref(acc), byref(series))
        if ERROR_CODE[error] == "DRV_SUCCESS":
            return acc.value
        else:
            return None

    def save_params_to_file(self, filepath=None):
        if filepath == None:
            data_file = df.create_file(set_current=False, mode='a')
        else:
            data_file = df.DataFile(filepath)
        data_file.create_dataset(name='AndorSettings', data=[], attrs=self.get_andor_parameters())
        data_file.close()

    def load_params_from_file(self, filepath=None):
        if filepath == None:
            data_file = df.open_file(set_current=False, mode='r')
        else:
            data_file = df.DataFile(filepath)
        if 'AndorSettings' in data_file.keys():
            self.set_andor_parameters(dict(data_file['AndorSettings'].attrs))
        else:
            self._logger.error('Load settings failed as "AndorSettings" does not exist')
        data_file.close()


parameters = dict(
    AvailableCameras=dict(Get=dict(cmdName='GetAvailableCameras', Outputs=(c_uint,)), value=None),
    channel=dict(value=0),
    PixelSize=dict(Get=dict(cmdName='GetAvailableCameras', Outputs=(c_float, c_float))),
    SoftwareWaitBetweenCaptures=dict(value=0),
    DetectorShape=dict(Get=dict(cmdName='GetDetector', Outputs=(c_int, c_int)), value=None),
    SerialNumber=dict(Get=dict(cmdName='GetCameraSerialNumber', Outputs=(c_int,)), value=None),
    HeadModel=dict(Get=dict(cmdName='GetHeadModel', Outputs=(c_char,) * 20), value=None),
    Capabilities=dict(Get=dict(cmdName='GetCapabilities', Outputs=(
        AndorCapabilities(sizeof(c_ulong) * 12, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0),)), value=None),
    AcquisitionMode=dict(Set=dict(cmdName='SetAcquisitionMode', Inputs=(c_int,)), value=None),
    TriggerMode=dict(Set=dict(cmdName='SetTriggerMode', Inputs=(c_int,)), value=None),
    ReadMode=dict(Set=dict(cmdName='SetReadMode', Inputs=(c_int,)), value=None),
    CropMode=dict(Set=dict(cmdName='SetCropMode', Inputs=(c_int,) * 3), value=None),
    IsolatedCropMode=dict(Set=dict(cmdName='SetIsolatedCropMode', Inputs=(c_int,) * 5), value=(0,)),
    AcquisitionTimings=dict(Get=dict(cmdName='GetAcquisitionTimings', Outputs=(c_float, c_float, c_float)),
                            value=None),
    AccumCycleTime=dict(Set=dict(cmdName='SetAccumulationCycleTime', Inputs=(c_float,)),
                        Finally='AcquisitionTimings'),
    KinCycleTime=dict(Set=dict(cmdName='SetKineticCycleTime', Inputs=(c_float,)),
                      Finally='AcquisitionTimings'),
    Exposure=dict(Set=dict(cmdName='SetExposureTime', Inputs=(c_float,)), Get_from_fixed_prop='AcquisitionTimings'),
    Image=dict(Set=dict(cmdName='SetImage', Inputs=(c_int,) * 6), value=None),
    NAccum=dict(Set=dict(cmdName='SetNumberAccumulations', Inputs=(c_int,)), value=1),
    NKin=dict(Set=dict(cmdName='SetNumberKinetics', Inputs=(c_int,)), value=1),
    FastKinetics=dict(Set=dict(cmdName='SetFastKineticsEx', Inputs=(c_int, c_int, c_float,) + (c_int,) * 4)),
    EMGain=dict(Set=dict(cmdName='SetEMCCDGain', Inputs=(c_int,)),
                Get=dict(cmdName='GetEMCCDGain', Outputs=(c_int,)), value=None),
    EMAdvancedGain=dict(Set=dict(cmdName='SetEMAdvanced', Inputs=(c_int,)), value=None),
    EMMode=dict(Set=dict(cmdName='SetEMCCDGainMode', Inputs=(c_int,)), value=None),
    EMGainRange=dict(Set=dict(cmdName='GetEMCCDGainRange', Outputs=(c_int,) * 2), value=None),
    Shutter=dict(Set=dict(cmdName='SetShutter', Inputs=(c_int,) * 4), value=None),
    CoolerMode=dict(Set=dict(cmdName='SetCoolerMode', Inputs=(c_int,)), value=None),
    FanMode=dict(Set=dict(cmdName='SetFanMode', Inputs=(c_int,)), value=None),
    ImageFlip=dict(Set=dict(cmdName='SetImageFlip', Inputs=(c_int,) * 2), value=None),
    ImageRotate=dict(Set=dict(cmdName='SetImageRotate', Inputs=(c_int,)), value=None),
    CurrentTemperature=dict(Get=dict(cmdName='GetTemperature', Outputs=(c_int,)), value=None),
    SetTemperature=dict(Set=dict(cmdName='SetTemperature', Inputs=(c_int,)), value=None),
    OutAmp=dict(Set=dict(cmdName='SetOutputAmplifier', Inputs=(c_int,))),
    FrameTransferMode=dict(Set=dict(cmdName='SetFrameTransferMode', Inputs=(c_int,)), value=None),
    SingleTrack=dict(Set=dict(cmdName='SetSingleTrack', Inputs=(c_int,) * 2), value=None),
    MultiTrack=dict(Set=dict(cmdName='SetMultiTrack', Inputs=(c_int,) * 3, Outputs=(c_int,) * 2)),
    FVBHBin=dict(Set=dict(cmdName='SetFVBHBin', Inputs=(c_int,)), value=1),
    Spool=dict(Set=dict(cmdName='SetSpool', Inputs=(c_int, c_int, c_char, c_int)), value=None),
    NumVSSpeed=dict(Get=dict(cmdName='GetNumberVSSpeeds', Outputs=(c_int,)), value=None),
    NumHSSpeed=dict(Get=dict(cmdName='GetNumberHSSpeeds', Outputs=(c_int,),
                             Input_params=(('channel', c_int), ('OutAmp', c_int))), value=None),
    VSSpeed=dict(Set=dict(cmdName='SetVSSpeed', Inputs=(c_int,)), Get_from_prop='VSSpeeds', GetAfterSet=True),
    VSSpeeds=dict(Get=dict(cmdName='GetVSSpeed', Inputs=(c_int,), Outputs=(c_float,), Iterator='NumVSSpeed'),
                  GetAfterSet=True),
    # why no work?
    HSSpeed=dict(Set=dict(cmdName='SetHSSpeed', Inputs=(c_int,), Input_params=(('OutAmp', c_int),)),
                 Get_from_prop='HSSpeeds'),
    HSSpeeds=dict(Get=dict(cmdName='GetHSSpeed', Inputs=(c_int,) * 2, Iterator='NumHSSpeed', Outputs=(c_float,),
                           Input_params=(('channel', c_int), ('OutAmp', c_int),))),
    NumPreAmp=dict(Get=dict(cmdName='GetNumberPreAmpGains', Outputs=(c_int,))),
    PreAmpGains=dict(Get=dict(cmdName='GetPreAmpGain', Inputs=(c_int,), Outputs=(c_float,), Iterator='NumPreAmp')),
    PreAmpGain=dict(Set=dict(cmdName='SetPreAmpGain', Inputs=(c_int,)), Get_from_prop='PreAmpGains', GetAfterSet=True),
    NumADChannels=dict(Get=dict(cmdName='GetNumberADChannels', Outputs=(c_int,))),
    ADChannel=dict(Set=dict(cmdName='SetADChannel', Inputs=(c_int,))),
    BitDepth=dict(Get=dict(cmdName='GetBitDepth', Inputs=(c_int,), Outputs=(c_int,), Iterator='NumADChannels'))
)
for param_name in parameters:
    setattr(AndorBase, param_name, AndorParameter(param_name))


class WaitThread(QtCore.QThread):
    def __init__(self, andor):
        QtCore.QThread.__init__(self, parent=None)
        self.Andor = andor

    def run(self):
        self.Andor._logger.info('Waiting for temperature to come up')
        temp = 30
        try:
            temp = self.Andor._dllWrapper('GetTemperature', outputs=(c_int(),))[0]
        except AndorWarning as warn:
            if warn.error_name != 'DRV_TEMP_OFF':
                raise warn
        if self.Andor.IsCoolerOn():
            self.Andor.CoolerOFF()
        if temp < 30:
            toggle = windll.user32.MessageBoxA(0, 'Camera is cold (%g), do you want to wait before ShutDown? '
                                                  '\n Not waiting can cause irreversible damage' % temp, '', 4)
            if toggle == 7:
                return
            else:
                while temp < -20:
                    self.Andor._logger.info('Waiting for temperature to come up. %g' % temp)
                    time.sleep(10)
                    try:
                        temp = self.Andor._dllWrapper('GetTemperature', outputs=(c_int(),))[0]
                    except AndorWarning as warn:
                        if warn.error_name != 'DRV_TEMP_OFF':
                            raise warn


ERROR_CODE = {
    20001: "DRV_ERROR_CODES",
    20002: "DRV_SUCCESS",
    20003: "DRV_VXNOTINSTALLED",
    20006: "DRV_ERROR_FILELOAD",
    20007: "DRV_ERROR_VXD_INIT",
    20010: "DRV_ERROR_PAGELOCK",
    20011: "DRV_ERROR_PAGE_UNLOCK",
    20013: "DRV_ERROR_ACK",
    20024: "DRV_NO_NEW_DATA",
    20026: "DRV_SPOOLERROR",
    20034: "DRV_TEMP_OFF",
    20035: "DRV_TEMP_NOT_STABILIZED",
    20036: "DRV_TEMP_STABILIZED",
    20037: "DRV_TEMP_NOT_REACHED",
    20038: "DRV_TEMP_OUT_RANGE",
    20039: "DRV_TEMP_NOT_SUPPORTED",
    20040: "DRV_TEMP_DRIFT",
    20050: "DRV_COF_NOTLOADED",
    20053: "DRV_FLEXERROR",
    20066: "DRV_P1INVALID",
    20067: "DRV_P2INVALID",
    20068: "DRV_P3INVALID",
    20069: "DRV_P4INVALID",
    20070: "DRV_INIERROR",
    20071: "DRV_COERROR",
    20072: "DRV_ACQUIRING",
    20073: "DRV_IDLE",
    20074: "DRV_TEMPCYCLE",
    20075: "DRV_NOT_INITIALIZED",
    20076: "DRV_P5INVALID",
    20077: "DRV_P6INVALID",
    20083: "P7_INVALID",
    20089: "DRV_USBERROR",
    20091: "DRV_NOT_SUPPORTED",
    20095: "DRV_INVALID_TRIGGER_MODE",
    20099: "DRV_BINNING_ERROR",
    20990: "DRV_NOCAMERA",
    20991: "DRV_NOT_SUPPORTED",
    20992: "DRV_NOT_AVAILABLE"
}
