"""
Lumenera camera wrapper for NPLab
=================================

This module wraps the lucam script from the legendary Christoph Gohlke 
<http://www.lfd.uci.edu/~gohlke/> and depends on having the lucam DLL installed
(most easily achieved by installing the Infinity Capture or Infinity Driver 
software from Lumenera's website).

@author: Richard Bowman (rwb27@cam.ac.uk)
"""

import sys
import time

import numpy as np
from nplab.utils.gui import qt, qtgui

import nplab.instrument.camera

import traits
from traits.api import HasTraits, Property, Instance, Float, String, Button, Bool, on_trait_change
import traitsui
from traitsui.api import View, Item, HGroup, VGroup
from traitsui.table_column import ObjectColumn
from enable.component_editor import ComponentEditor

import ctypes
asVoidPtr = ctypes.pythonapi.PyCObject_AsVoidPtr #this function converts PyCObject to void *, why is it not in ctypes natively...?
asVoidPtr.restype = ctypes.c_void_p #we need to set the result and argument types of the imported function
asVoidPtr.argtypes = [ctypes.py_object]

try:
    import lucam
except WindowsError:
    explanation="""
WARNING: could not open the lucam driver.
    
Make sure you have installed the Infinity drivers (included with Infinity
Capture), and that its version matches your Python architecture (64 or 32 bit).
"""
    try:
        import traitsui.message
        traitsui.message.error(explanation,"Infinity Driver Missing", buttons=["OK"])
    except Exception as e:
        print "uh oh, problem with the message..."
        print e
        pass
    finally:
        raise ImportError(explanation) 

from nplab.instrument.camera import Camera, CameraParameter, ImageClickTool

class LumeneraCameraParameter(CameraParameter):
    value = Property(Float(np.NaN))
    name = String()
    
    def __init__(self, parent, parameter_name):
        self.parent = parent
        self.name = parameter_name.title().replace('_',' ') #format name prettily
        try:
            self._parameter_name = parameter_name
            assert parameter_name in parent.parameter_names()
        except AttributeError:
            raise AttributeError("%s is not a valid capture property, try CameraParameter.list_names()")
            
    def _get_value(self):
        return self.parent.cam.GetProperty(self._parameter_name)[0]
        
    def _set_value(self, value):
        return self.parent.cam.SetProperty(self._parameter_name, value)
        
class LumeneraCamera(Camera):
    last_frame_time = -1
    fps = -1
    latest_frame = traits.trait_numeric.Array(dtype=np.uint8,shape=(None, None, 3))

    edit_properties = Button
    edit_video_properties = Button
    edit_camera_properties = Button
    video_priority = Bool(True)
    exposure = Property(Float)
    gain = Property(Float)
    
    traits_view = View(VGroup(
                Item(name="image_plot",editor=ComponentEditor(),show_label=False,springy=True),
                HGroup(
                    VGroup(
                        VGroup(
                            Item(name="exposure"),
                            Item(name="gain"),
                            Item(name="live_view")
                            ), #the vgroup is a trick to make the column narrower
                        HGroup(
                            Item(name="edit_properties",show_label=False),
                            Item(name="edit_video_properties",show_label=False),
                            Item(name="edit_camera_properties",show_label=False),
                        ),
                    springy=False),
                    VGroup(
                        Item(name="take_snapshot",show_label=False),
                        HGroup(Item(name="description")),
                        Item(name="save_snapshot",show_label=False),
                        Item(name="save_jpg_snapshot",show_label=False),
                        HGroup(Item(name="video_priority")),
                    springy=False),
                springy=True),
            layout="split"), kind="live",resizable=True,width=500,height=600,title="Camera")
    def __init__(self,camera_number=1):
        self.cam = lucam.Lucam(camera_number) #lucam is 1-indexed...
        self._cameraIsStreaming = False
        
        super(LumeneraCamera,self).__init__() #NB this comes after setting up the hardware
     
    def close(self):
        """Stop communication with the camera and allow it to be re-used."""
        super(LumeneraCamera, self).close()
        self.cam.CameraClose()
        
    def raw_snapshot(self, suppress_errors = False, video_priority = None):
        """Take a snapshot and return it.  Bypass filters etc.
        
        If video_priority is specified, don't interrupt video streaming and
        just return the latest frame.  If it's set to false, stop the video
        stream, take a snapshot, and re-start the video stream."""
        if video_priority is None:
            video_priority = self.video_priority
        if self._cameraIsStreaming and video_priority:
            #TODO add logic that waits for the next frame
            return True, self.latest_frame #if we're streaming video, just use the latest frame
        
        with self.acquisition_lock:
            for i in range(10):
                try:
                    frame = self.cam.TakeSnapshot()
                    assert frame is not None, "Failed to capture a frame"
                    frame_pointer = frame.ctypes.data_as(ctypes.POINTER(ctypes.c_byte))
                    return True, self.convert_frame(frame_pointer, np.product(frame.shape))
                except Exception as e:
                    print "Attempt number {0} failed to capture a frame from the camera: {1}".format(i,e)
        print "Camera.raw_snapshot() has failed to capture a frame."
        if not suppress_errors:
            raise IOError("Dropped too many frames from camera :(")
        else:
            return False, None
        
    def _streamingCallback(self, context, frame_pointer, frame_size):
        """This function is called on each frame that comes back from the camera when it's streaming.
        
        We keep track of the frame rate, and convert each frame to RGB so we 
        can store it in latest_image and thus update the GUI.
        """
        now = time.clock()
        self.fps = 1/(now - self.last_frame_time)
        self.last_frame_time = now
        try:
            #last_frame = np.ctypeslib.as_array(frame_pointer, shape=(h, w)).astype(np.ubyte, copy=True)
            self.latest_frame = self.convert_frame(frame_pointer, frame_size)
        except:
            print "invalid frame size"        
    def convert_frame(self, frame_pointer, frame_size):
        """Convert a frame from the camera to an RGB numpy array."""
        f = self.cam.GetFormat()[0]
        w, h = f.width // (f.binningX * f.subSampleX), f.height // (f.binningY * f.subSampleY)
        assert frame_size == w*h, "The frame size did not match the image format!"
        converted_frame = self.cam.ConvertFrameToRgb24(f, frame_pointer) #actually convert the frame
        return converted_frame[:,:,::-1] #for some reason frames come back BGR - flip them to RGB
    def start_streaming(self):
        """Start streaming video from the camera as a preview."""
        self.cam.StreamVideoControl('start_streaming')
       # time.sleep(0.5)
        self._callback_id = self.cam.AddStreamingCallback(self._streamingCallback)
        self._cameraIsStreaming = True
    def stop_streaming(self):
        """Stop streaming video from the camera."""
        self.cam.StreamVideoControl('stop_streaming')
       # time.sleep(0.5)
        self.cam.RemoveStreamingCallback(self._callback_id)
        self._cameraIsStreaming = False
        
    def parameter_names(self):
        return lucam.Lucam.PROPERTY.keys()
    
    def initialise_parameters(self):
        self.parameters = [LumeneraCameraParameter(self,n) for n in self.parameter_names()]
        
    def get_metadata(self):
        """Return a dictionary of camera settings and parameters."""
        ret = super(LumeneraCamera, self).get_metadata()
        
        camid=self.cam.GetCameraId()
        version = self.cam.QueryVersion()
        interface = self.cam.QueryExternInterface()
        frame, fps = self.cam.GetFormat()
        depth = self.cam.GetTruePixelDepth()
        
        pixformat = 'raw8 raw16 RGB24 YUV422 Count Filter RGBA32 RGB48'.split()
        ret['camera_id'] = camid
        ret['camera_model'] = lucam.CAMERA_MODEL.get(camid, "Unknown")
        ret['serial_number'] = version.serialnumber
        ret['firmware_version'] = lucam.print_version(version.firmware)
        ret['FPGA_version'] = lucam.print_version(version.fpga)
        ret['API_version'] = lucam.print_version(version.api)
        ret['driver_version'] = lucam.print_version(version.driver)
        ret['Interface'] = lucam.Lucam.EXTERN_INTERFACE[interface]
        ret['image_offset'] = (frame.xOffset, frame.yOffset)
        ret['image_size'] = (frame.width // frame.binningX,
                                     frame.height // frame.binningY)
        if frame.flagsX:
            ret['binning'] = (frame.binningX, frame.binningY)
        else:
            ret['subsampling'] = (frame.subSampleX, frame.subSampleY)
        ret['pixel_format'] = pixformat[frame.pixelFormat]
        ret['bit_depth'] = (depth if frame.pixelFormat else 8)
        ret['frame_rate'] = fps
        return ret
    
    def _edit_properties_fired(self):
        self.cam.DisplayPropertyPage(None)
    def _edit_video_properties_fired(self):
        self.cam.DisplayVideoFormatPage(None)
        
    def _get_exposure(self):
        return self.cam.GetProperty('exposure')[0]
    def _set_exposure(self, exp):
        self.cam.SetProperty('exposure', exp)
    def _get_gain(self):
        return self.cam.GetProperty('gain')[0]
    def _set_gain(self, exp):
        self.cam.SetProperty('gain', exp)
    
    def _live_view_changed(self):
        """Turn live view on and off"""
        if self.live_view==True:
            self.start_streaming()
        else:
            self.stop_streaming()
#            print "starting live view thread"
#            try:
#                self._live_view_stop_event = threading.Event()
#                self._live_view_thread = threading.Thread(target=self._live_view_function)
#                self._live_view_thread.start()
#            except AttributeError as e: #if any of the attributes aren't there
#                print "Error:", e
#        else:
#            print "stopping live view thread"
#            try:
#                self._live_view_stop_event.set()
#                self._live_view_thread.join()
#                del(self._live_view_stop_event, self._live_view_thread)
#            except AttributeError:
#                raise Exception("Tried to stop live view but it doesn't appear to be running!")
#    def _live_view_function(self):
#        """this function should only EVER be executed by _live_view_changed."""
#        while not self._live_view_stop_event.wait(timeout=0.1):
#            self.update_latest_frame()