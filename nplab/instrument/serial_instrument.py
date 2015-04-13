# -*- coding: utf-8 -*-
"""
Serial Instrument interface

@author: Richard Bowman
"""
from traits.api import HasTraits, Bool, Int, Str, Button, Array, Enum, List
import nplab
from nplab.instrument.message_bus_instrument import MessageBusInstrument
import threading
import serial
import serial.tools.list_ports
from serial import FIVEBITS, SIXBITS, SEVENBITS, EIGHTBITS
from serial import PARITY_NONE, PARITY_EVEN, PARITY_ODD, PARITY_MARK, PARITY_SPACE
from serial import STOPBITS_ONE, STOPBITS_ONE_POINT_FIVE, STOPBITS_TWO
import io
import re

class SerialInstrument(MessageBusInstrument):
    """
    An instrument primarily using serial communcations
    """
    port_settings = {}
    """A dictionary of serial port settings.  It is passed as the keyword
    arguments to the constructor of the underlying serial port object, so
    see the documentation for pyserial for full explanations.
    
    port
        Device name or port number number or None.
    baudrate
        Baud rate such as 9600 or 115200 etc.
    bytesize
        Number of data bits. Possible values: FIVEBITS, SIXBITS, SEVENBITS, EIGHTBITS
    parity
        Enable parity checking. Possible values: PARITY_NONE, PARITY_EVEN, PARITY_ODD PARITY_MARK, PARITY_SPACE
    stopbits
        Number of stop bits. Possible values: STOPBITS_ONE, STOPBITS_ONE_POINT_FIVE, STOPBITS_TWO
    timeout
        Set a read timeout value.
    xonxoff
        Enable software flow control.
    rtscts
        Enable hardware (RTS/CTS) flow control.
    dsrdtr
        Enable hardware (DSR/DTR) flow control.
    writeTimeout
        Set a write timeout value.
    interCharTimeout
        Inter-character timeout, None to disable (default).
    """
    
    _serial_port_lock = threading.Lock()
    
    def __init__(self, port=None):
        """
        Set up the serial port and so on.
        """
        self.open(port, False)
    def open(self, port=None, quiet=True):
        """Open communications with the serial port.
        
        If no port is specified, it will attempt to autodetect.  If quiet=True
        then we don't warn when ports are opened multiple times.
        """
        if hasattr(self,'ser') and self.ser.isOpen():
            if not quiet: print "Warning: attempted to open an already-open port!"
            return
        if port is None: port=self.find_port()
        assert port is not None, "We don't have a serial port to open, meaning you didn't specify a valid port and autodetection failed.  Are you sure the instrument is connected?"
        self.ser = serial.Serial(port,**self.port_settings)
        self.ser_io = io.TextIOWrapper(io.BufferedRWPair(self.ser, self.ser, 1),  
                                       newline = self.termination_character,
                                       line_buffering = True)
        #the block above wraps the serial IO layer with a text IO layer
        #this allows us to read/write in neat lines.  NB the buffer size must
        #be set to 1 byte for maximum responsiveness.
        assert self.test_communications(), "The instrument doesn't seem to be responding.  Did you specify the right port?"
    def close(self):
        try:
            self.ser.close()
        except Exception as e:
            print "The serial port didn't close cleanly:", e
    def __del__(self):
        self.close()
    def write(self,query_string):
        """Write a string to the serial port"""
        assert self.ser.isOpen(), "Warning: attempted to write to the serial port before it was opened.  Perhaps you need to call the 'open' method first?"
        if self.ser.outWaiting()>0: self.ser.flushOutput() #ensure there's nothing waiting
        self.ser.write(query_string+self.termination_character)
    def flush_input_buffer(self):
        """Make sure there's nothing waiting to be read, and clear the buffer if there is."""
        if self.ser.inWaiting()>0: self.ser.flushInput()
    def readline(self, timeout=None):
        """Read one line from the serial port."""
        return self.ser_io.readline().replace(self.termination_character,"\n")
    def test_communications(self):
        """Check if the device is available on the current port.  
        
        This should be overridden by subclasses.  Assume the port has been
        successfully opened and the settings are as defined by self.port_settings.
        Usually this function sends a command and checks for a known reply."""
        return True
    