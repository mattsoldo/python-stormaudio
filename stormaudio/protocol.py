"""Module to maintain AVR state information and network interface."""
import asyncio
import logging
import time
import numbers

__all__ = ('AVR')

# In Python 3.4.4, `async` was renamed to `ensure_future`.
try:
    ensure_future = asyncio.ensure_future
except AttributeError:
    ensure_future = getattr(asyncio, 'async')

# These properties apply even when the AVR is powered off
ATTR_CORE = {'Z1POW', 'IDM'}

LOOKUP = {}
LOOKUP['ssp.procstate'] = {'description': 'Processor Status',
                            '0': 'Off',
                            '1': 'Starting / Stopping',
                            '2': 'On'}
LOOKUP['ssp.power'] = {'description': 'Power'}
LOOKUP['ssp.version'] = {'description': 'Version Number'}
LOOKUP['ssp.brand'] = {'description': 'Brand'}
LOOKUP['ssp.msgstatus'] = {'description': 'Message Status Group',
                            '0': '',
                            '1': 'Backup parameters in progress',
                            '2': 'Restore parameters in progress',
                            '3': 'Selective parameters in progress',
                            '4': 'Reset parameters in progress',
                            '5': 'Firmware upgrade in progress',
                            '6': 'Loading Dirac room calibration',
                            '98': 'String Message',
                            '99': ''}
LOOKUP['ssp.input'] = {'description': 'Input'}
LOOKUP['ssp.input.list'] = {'description': 'List all configured inputs'}
LOOKUP['ssp.preset'] = {'description': 'Preset'}
LOOKUP['ssp.surroundmode'] = {'description': 'Surround Mode'}
LOOKUP['ssp.allowedmode'] = {'description': 'Active Surround Modes'}
LOOKUP['ssp.speaker'] = {'description': 'Speaker Config ID'}
LOOKUP['ssp.mute'] = {'description': 'Mute status'}
LOOKUP['ssp.dim'] = {'description': 'Dim status'}
LOOKUP['ssp.vol'] = {'description': 'Volume Level'}
LOOKUP['ssp.bass'] = {'description': 'Bass Level'}
LOOKUP['ssp.treb'] = {'description': 'Treble Level'}
LOOKUP['ssp.brightness'] = {'description': 'Brightness Level'}
LOOKUP['ssp.c_en'] = {'description': 'Center Enhance'}
LOOKUP['ssp.s_en'] = {'description': 'Surround Enhance'}
LOOKUP['ssp.lipsync'] = {'description': 'Lip Sync Level'}
LOOKUP['ssp.sub_en'] = {'description': 'Subwoofer Enhance'}
LOOKUP['ssp.aurostrength'] = {'description': 'Auro Strength'}
LOOKUP['ssp.auropreset'] = {'description': 'Auro Preset',
                            '0': 'Small',
                            '1': 'Medium',
                            '2': 'Large',
                            '3': 'Speech'}
LOOKUP['ssp.drc'] = {'description': 'DRC Status'}
LOOKUP['ssp.cspread'] = {'description': 'Center Spread'}
LOOKUP['ssp.dialogcontrol'] = {'description': 'Dialog Control'}
LOOKUP['ssp.dialognorm'] = {'description': 'Dialog Norm'}
LOOKUP['ssp.IMAXMode'] = {'description': 'IMAX Mode'}
LOOKUP['ssp.spheraudioeffect'] = {'description': 'SphereAudio Effect',
                            '0': 'ByPass',
                            '1': 'Lounge',
                            '2': 'Home Cinema',
                            '3': 'Concert',
                            '4': 'Cinema'}
LOOKUP['ssp.lfedim'] = {'description': 'LFE Dim'}
LOOKUP['ssp.zones.list'] = {'description': ''}
LOOKUP['ssp.frontpanel.color'] = {'description': 'Front Panel Color'}
LOOKUP['ssp.frontpanel.stbybright'] = {'description': 'Front Panel Standby Brightness'}
LOOKUP['ssp.frontpanel.actbright'] = {'description': 'Front Panel Active Brightness'}
LOOKUP['ssp.frontpanel.stbydelay'] = {'description': 'Front Panel Standby Delay'}
LOOKUP['ssp.fs'] = {'description': 'Sample Rate'}
LOOKUP['ssp.stream'] = {'description': 'Stream Type'}
LOOKUP['ssp.format'] = {'description': 'Format Code'}
LOOKUP['ssp.trig1'] = {'description': 'Trigger 1'}

# pylint: disable=too-many-instance-attributes, too-many-public-methods
class AVR(asyncio.Protocol):
    """The Anthem AVR IP control protocol handler."""

    def __init__(self, update_callback=None, loop=None, connection_lost_callback=None):
        """Protocol handler that handles all status and changes on AVR.

        This class is expected to be wrapped inside a Connection class object
        which will maintain the socket and handle auto-reconnects.

            :param update_callback:
                called if any state information changes in device (optional)
            :param connection_lost_callback:
                called when connection is lost to device (optional)
            :param loop:
                asyncio event loop (optional)

            :type update_callback:
                callable
            :type: connection_lost_callback:
                callable
            :type loop:
                asyncio.loop
        """
        self._loop = loop
        self.log = logging.getLogger(__name__)
        self._connection_lost_callback = connection_lost_callback
        self._update_callback = update_callback
        self.buffer = ''
        self._input_names = {}
        self._input_numbers = {}
        self._poweron_refresh_successful = False
        self.transport = None

        for key in LOOKUP:
            setattr(self, '_'+key, '')

    def refresh_core(self):
        """Query device for all attributes that exist regardless of power state.

        This will force a refresh for all device queries that are valid to
        request at any time.  It's the only safe suite of queries that we can
        make if we do not know the current state (on or off+standby).

        This does not return any data, it just issues the queries.
        """
        self.log.info('Sending out mass query for all attributes')
        for key in ATTR_CORE:
            self.query(key)

    def poweron_refresh(self):
        """Keep requesting all attributes until it works.

        Immediately after a power on event (POW1) the AVR is inconsistent with
        which attributes can be successfully queried.  When we detect that
        power has just been turned on, we loop every second making a bulk
        query for every known attribute.  This continues until we detect that
        values have been returned for at least one input name (this seems to
        be the laggiest of all the attributes)
        """
        if self._poweron_refresh_successful:
            return
        else:
            self.refresh_all()
            self._loop.call_later(2, self.poweron_refresh)


    def refresh_all(self):
        """Query device for all attributes that are known.

        This will force a refresh for all device queries that the module is
        aware of.  In theory, this will completely populate the internal state
        table for all attributes.

        This does not return any data, it just issues the queries.
        """
        self.log.info('refresh_all')
        for key in LOOKUP:
            self.query(key)


    #
    # asyncio network functions
    #

    def connection_made(self, transport):
        """Called when asyncio.Protocol establishes the network connection."""
        self.log.info('Connection established to AVR')
        self.transport = transport

        #self.transport.set_write_buffer_limits(0)
        limit_low, limit_high = self.transport.get_write_buffer_limits()
        self.log.debug('Write buffer limits %d to %d', limit_low, limit_high)

        self.command('ECH1')
        self.refresh_core()

    def data_received(self, data):
        """Called when asyncio.Protocol detects received data from network."""
        self.buffer += data.decode()
        self.log.debug('Received %d bytes from AVR: %s', len(self.buffer), self.buffer)
        self._assemble_buffer()

    def connection_lost(self, exc):
        """Called when asyncio.Protocol loses the network connection."""
        if exc is None:
            self.log.warning('eof from receiver?')
        else:
            self.log.warning('Lost connection to receiver: %s', exc)

        self.transport = None

        if self._connection_lost_callback:
            self._loop.call_soon(self._connection_lost_callback)

    def _assemble_buffer(self):
        """Split up received data from device into individual commands.

        Data sent by the device is a sequence of datagrams separated by
        semicolons.  It's common to receive a burst of them all in one
        submission when there's a lot of device activity.  This function
        disassembles the chain of datagrams into individual messages which
        are then passed on for interpretation.
        """
        self.transport.pause_reading()

        for message in self.buffer.split('\n'):
            if message != '':
                self.log.debug('assembled message '+message)
                self._parse_message(message)

        self.buffer = ""

        self.transport.resume_reading()
        return

    def _populate_inputs(self, total):
        """Request the names for all active, configured inputs on the device.

        Once we learn how many inputs are configured, this function is called
        which will ask for the name of each active input.
        """
        total = total + 1
        for input_number in range(1, total):
            self.query('ISN'+str(input_number).zfill(2))

    def _parse_message(self, data):
        """Interpret each message datagram from device and do the needful.

        This function receives datagrams from _assemble_buffer and inerprets
        what they mean.  It's responsible for maintaining the internal state
        table for each device attribute and also for firing the update_callback
        function (if one was supplied)
        """
        recognized = False
        newdata = False

        if data.startswith('ssp.zones'):
            self.log.warning('Zones Control Unsupported : %s', data)
            recognized = True
        else:
            for key in LOOKUP:
                if data.startswith(key):
                    recognized = True

                    # Value is the last item in string separated by dots 
                    # don't split more than twice to account for decimal in volume
                    value = data.split('.',2)[-1].strip('[]')
                    print("Parsed Value {} for key {} with data {}".format(value, key, data))
                    oldvalue = getattr(self, '_'+key)
                    if oldvalue != value:
                        changeindicator = 'New Value'
                        newdata = True
                    else:
                        changeindicator = 'Unchanged'

                    if key in LOOKUP:
                        if 'description' in LOOKUP[key]:
                            if value in LOOKUP[key]:
                                self.log.info('%s: %s (%s) -> %s (%s)',
                                              changeindicator,
                                              LOOKUP[key]['description'], key,
                                              LOOKUP[key][value], value)
                            else:
                                self.log.info('%s: %s (%s) -> %s',
                                              changeindicator,
                                              LOOKUP[key]['description'], key,
                                              value)
                    else:
                        self.log.info('%s: %s -> %s', changeindicator, key, value)

                    setattr(self, '_'+key, value)

                    if key == 'ssp.power' and value == '1' and oldvalue == '0':
                        self.log.info('Power on detected, refreshing all attributes')
                        self._poweron_refresh_successful = False
                        self._loop.call_later(1, self.poweron_refresh)

                    break

            # input_number = int(data[3:5])
            # value = data[5:]

            # oldname = self._input_names.get(input_number, '')

            # if oldname != value:
                # self._input_numbers[value] = input_number
                # self._input_names[input_number] = value
                # self.log.info('New Value: Input %d is called %s', input_number, value)
                # newdata = True

        if newdata:
            if self._update_callback:
                self._loop.call_soon(self._update_callback, data)
        else:
            self.log.debug('no new data encountered')

        if not recognized:
            self.log.warning('Unrecognized response: %s', data)

    def query(self, item):
        """Issue a raw query to the device for an item.
        
        Example: query('ssp.vol')
 
        This function is used to request that the device supply the current
        state for a data item as described in the Storm Audio IP protocoal API.
        """
        self.command(item)

    def set_value(self, command, value):
        # If the value is one of the following
        # then the command format is different
        special_values = ['on','off','toggle','up','down']

        if value in special_values:
            command_to_send = '{}.{}\n'.format(command,value)
        else:
            command_to_send = '{}.[{}]\n'.format(command,value)
        self.command(command_to_send.encode())
    
    def command(self, command):
        """Issue a raw command to the device.

        This function is used to update a data item on the device.  It's used
        to cause activity or change the configuration of the AVR.  Normal
        interaction with this module will not require you to make raw device
        queries with this function, but the method is exposed in case there's a
        need that's not otherwise met by the abstraction methods defined
        elsewhere.

            :param command: Any command as documented in the Anthem API
            :type command: str

        :Example:

        >>> command('Z1VOL-50')
        """
        self.log.debug('> %s', command)
        try:
            self.transport.write(command)
            time.sleep(0.01)
        except:
            self.log.warning('No transport found, unable to send command')


    #
    # Volume and Attenuation handlers.  The Anthem tracks volume internally as
    # an attenuation level ranging from -90dB (silent) to 0dB (bleeding ears)
    #
    # We expose this in three methods for the convenience of downstream apps
    # which will almost certainly be doing things their own way:
    #
    #   - attenuation (-90 to 0)
    #   - volume (0-100)
    #   - volume_as_percentage (0-1 floating point)
    #

    @property
    def attenuation(self):
        """Current volume attenuation in dB (read/write).

        You can get or set the current attenuation value on the device with this
        property.  Valid range from -90 to 0.

        :Examples:

        >>> attvalue = attenuation
        >>> attenuation = -50
        """
        try:
            return int(getattr(self,ssp.vol))
        except ValueError:
            return -100
        except NameError:
            return -100

    @attenuation.setter
    def attenuation(self, value):
        if isinstance(value, numbers.Number) and -100 < value <= 0:
            self.log.debug('Setting attenuation to '+str(value))
            self.set_value('ssp.vol',value)

    @property
    def volume(self):
        """Current volume level (read/write).

        You can get or set the current volume value on the device with this
        property.  Valid range from 0 to 100.

        :Examples:

        >>> volvalue = volume
        >>> volume = 20
        """
        return getattr(self, 'ssp.vol')
        # return self.query(ssp.vol)

    @volume.setter
    def volume(self, value):
        if isinstance(value, int) and 0 <= value < 100:
            self.set_value('ssp.vol',-1*value)

    @property
    def volume_as_percentage(self):
        """Current volume as percentage (read/write).

        You can get or set the current volume value as a percentage.  Valid
        range from 0 to 1 (float).

        :Examples:

        >>> volper = volume_as_percentage
        >>> volume_as_percentage = 0.20
        """
        return (100 - self.volume) / 100

    @volume_as_percentage.setter
    def volume_as_percentage(self, value):
        if isinstance(value, number.Number) and 0 < value <= 1:
            self.volume = 100*(1-value)

    #
    # Internal assistant functions for unified handling of boolean
    # properties that are read/write
    #

    def _get_boolean(self, key):
        keyname = '_'+key
        try:
            value = getattr(self, keyname)
            return bool(int(value))
        except ValueError:
            return False
        except AttributeError:
            return False

    def _set_boolean(self, key, value):
        if value is True:
            self.command(key+'1')
        else:
            self.command(key+'0')

    #
    # Boolean properties and corresponding setters
    #

    @property
    def power(self):
        """Report if device powered on or off (read/write).

        Returns and expects a boolean value.
        """
        return self._get_boolean('Z1POW')

    @power.setter
    def power(self, value):
        self._set_boolean('Z1POW', value)
        self._set_boolean('Z1POW', value)

    @property
    def txstatus(self):
        """Current TX Status of the device (read/write).

        When enabled, all commands, status changes, and control information
        are reported through the Ethernet and RS-232 connections.  Do not
        disable this setting, the stormaudio pacakge requires it.

        It is explicitly set to True whenever this module connects to the AVR,
        but I'll still let you disable it though, because I believe in aiming
        loaded guns right at my own feet.

            :param arg1: setting
            :type arg1: boolean
        """
        return self._get_boolean('ECH')

    @txstatus.setter
    def txstatus(self, value):
        self._set_boolean('ECH', value)

    @property
    def standby_control(self):
        """Current Standby IP Control of the device (read/write).

        When disabled, the AVM/MRX goes into a low-consumption standby mode and
        does not sense IP commands while in it. To make it respond to a
        power-on command or to keep DTS Play-Fi connected to the network so it
        can be used immediately after power- on, enable this setting.

            :param arg1: setting
            :type arg1: boolean
        """
        return self._get_boolean('SIP')

    @standby_control.setter
    def standby_control(self, value):
        self._set_boolean('SIP', value)

    @property
    def arc(self):
        """Current ARC (Anthem Room Correction) on or off (read/write)."""
        return self._get_boolean('Z1ARC')

    @arc.setter
    def arc(self, value):
        self._set_boolean('Z1ARC', value)

    @property
    def mute(self):
        """Mute on or off (read/write)."""
        return self._get_boolean('Z1MUT')

    @mute.setter
    def mute(self, value):
        self._set_boolean('Z1MUT', value)

    #
    # Read-only text properties
    #

    @property
    def model(self):
        """Device Model Name (read-only)."""
        return self._IDM or "Unknown Model"

    @property
    def swversion(self):
        """Software version (read-only)."""
        return self._IDS or "Unknown Version"

    @property
    def region(self):
        """Region (read-only)."""
        return self._IDR or "Unknown Region"

    @property
    def build_date(self):
        """Software build date (read-only)."""
        return self._IDB or "Unknown Build Date"

    @property
    def hwversion(self):
        """Hardware version (read-only)."""
        return self._IDH or "Unknown Version"

    @property
    def macaddress(self):
        """Network MCU MAC address (read-only)."""
        return self._IDN or "00:00:00:00:00:00"

    @property
    def audio_input_name(self):
        """Current audio input format short description (read-only)."""
        return self._Z1AIN or "Unknown"

    @property
    def audio_input_ratename(self):
        """Current audio input format sample or bit rate (read-only)."""
        return self._Z1AIR or "Unknown"

    #
    # Read-only raw numeric properties
    #

    def _get_integer(self, key):
        keyname = '_'+key
        if hasattr(self, keyname):
            value = getattr(self, keyname)
        try:
            return int(value)
        except ValueError:
            return

    @property
    def dolby_dialog_normalization(self):
        """Query Dolby Digital dialog normalization amount (read-only).

        Returns value in dB of normalization (if applicable).
        """
        return self._get_integer('Z1DIA')

    @property
    def horizontal_resolution(self):
        """Query active horizontal video resolution (in pixels)."""
        return self._get_integer('Z1IRH')

    @property
    def vertical_resolution(self):
        """Query active vertical video resolution (in pixels)."""
        return self._get_integer('Z1IRV')

    @property
    def audio_input_bitrate(self):
        """Query audio input bitrate (in kbps).

        For Analog/PCM inputs this is equal to the sample rate multiplied by
        the bit depth and the number of channels.
        """
        return self._get_integer('Z1BRT')

    @property
    def audio_input_samplerate(self):
        """Query audio input sampling rate (kHz)."""
        return self._get_integer('Z1SRT')

    #
    # Helper functions for working with raw/text multi-property items
    #
    #
    def _get_multiprop(self, key, mode='raw'):
        keyname = '_'+key

        if hasattr(self, keyname):
            rawvalue = getattr(self, keyname)
            value = rawvalue

            if key in LOOKUP:
                if rawvalue in LOOKUP[key]:
                    value = LOOKUP[key][rawvalue]

            if mode == 'raw':
                return rawvalue
            else:
                return value
        else:
            return

    #
    # Read/write properties with raw and text options
    #
    #
    @property
    def panel_brightness(self):
        """Current front panel brightness value (int 0-3) (read-write).

        0=off, 1=low, 2=medium, 3=high
        """
        return self._get_multiprop('FPB', mode='raw')

    @property
    def panel_brightness_text(self):
        """Current front panel brighness value (str) (read-only)."""
        return self._get_multiprop('FPB', mode='text')

    @panel_brightness.setter
    def panel_brightness(self, number):
        if isinstance(number, int):
            if 0 <= number <= 3:
                self.log.info('Switching panel brightness to '+str(number))
                self.command('FPB'+str(number))

    @property
    def audio_listening_mode(self):
        """Current audio listening mode (00-16) (read-write).

        Audio Listening Mode: 00=None, 01=AnthemLogic-Movie,
        02=AnthemLogic-Music, 03=PLIIx Movie, 04=PLIIx Music, 05=Neo:6 Cinema,
        06=Neo:6 Music, 07=All Channel Stereo*, 08=All-Channel Mono*, 09=Mono*,
        10=Mono-Academy*, 11=Mono(L)*, 12=Mono(R)*, 13=High Blend*, 14=Dolby
        Surround, 15=Neo:X-Cinema, 16=Neo:X-Music, na=cycle to next applicable,
        pa=cycle to previous applicable.  *Applicable to 2-channel source only.
        Some options are not available in all models or under all
        circumstances.
        """
        return self._get_multiprop('Z1ALM', mode='raw')

    @property
    def audio_listening_mode_text(self):
        """Current audio listening mode (str) (read-only)."""
        return self._get_multiprop('Z1ALM', mode='text')

    @audio_listening_mode.setter
    def audio_listening_mode(self, number):
        if isinstance(number, int):
            if 0 <= number <= 16:
                self.log.info('Switching audio listening mode to '+str(number))
                self.command('Z1ALM'+str(number).zfill(2))

    @property
    def dolby_dynamic_range(self):
        """Current Dolby Dynamic Range setting (0-2) (read-write).

        Applies to Dolby Digital 5.1 source only.

        0=Normal, 1=Reduced, 2=Late Night.
        """
        return self._get_multiprop('Z1DYN', mode='raw')

    @property
    def dolby_dynamic_range_text(self):
        """Current Dolby Dynamic Range setting (str) (read-only)."""
        return self._get_multiprop('Z1DYN', mode='text')

    @dolby_dynamic_range.setter
    def dolby_dynamic_range(self, number):
        if isinstance(number, int):
            if 0 <= number <= 2:
                self.log.info('Switching Dolby dynamic range to '+str(number))
                self.command('Z1DYN'+str(number))

    #
    # Read-only properties with raw and text options
    #

    @property
    def video_input_resolution(self):
        """Current video input resolution (0-14) (read-only).

        0=no input, 1=other, 2=1080p60, 3=1080p50, 4=1080p24, 5=1080i60,
        6=1080i50, 7=720p60, 8=720p50, 9=576p50, 10=576i50, 11=480p60,
        12=480i60, 13=3D, 14=4k
        """
        return self._get_multiprop('Z1VIR', mode='raw')

    @property
    def video_input_resolution_text(self):
        """Current video input resolution (str) (read-only)."""
        return self._get_multiprop('Z1VIR', mode='text')

    @property
    def audio_input_channels(self):
        """Current audio input channels (0-7) (read-only).

        0=no input, 1=other, 2=mono (center channel only), 3=2-channel,
        4=5.1-channel, 5=6.1-channel, 6=7.1-channel, 7=Atmos
        """
        return self._get_multiprop('Z1AIC', mode='raw')

    @property
    def audio_input_channels_text(self):
        """Current audio input channels (str) (read-only)."""
        return self._get_multiprop('Z1AIC', mode='text')

    @property
    def audio_input_format(self):
        """Current audio input format (0-6) (read-only).

        0=no input, 1=Analog, 2=PCM, 3=Dolby, 4= DSD, 5=DTS, 6=Atmos.
        """
        return self._get_multiprop('Z1AIF', mode='raw')

    @property
    def audio_input_format_text(self):
        """Current audio input format (str) (read-only)."""
        return self._get_multiprop('Z1AIF', mode='text')

    #
    # Input number and lists
    #

    @property
    def input_list(self):
        """List of all enabled inputs."""
        return list(self._input_numbers.keys())

    @property
    def input_name(self):
        """Name of currently active input (read-write)."""
        return self._input_names.get(self.input_number, "Unknown")

    @input_name.setter
    def input_name(self, value):
        number = self._input_numbers.get(value, 0)
        if number > 0:
            self.input_number = number

    @property
    def input_number(self):
        """Number of currently active input (read-write)."""
        return self._get_integer('Z1INP')

    @input_number.setter
    def input_number(self, number):
        if isinstance(number, int):
            if 1 <= number <= 99:
                self.log.info('Switching input to '+str(number))
                self.command('Z1INP'+str(number))

    #
    # Miscellany
    #

    @property
    def dump_rawdata(self):
        """Return contents of transport object for debugging forensics."""
        if hasattr(self, 'transport'):
            attrs = vars(self.transport)
            return ', '.join("%s: %s" % item for item in attrs.items())

    @property
    def test_string(self):
        """I really do."""
        return 'I like cows'
