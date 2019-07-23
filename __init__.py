# Copyright 2017 Mycroft AI Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import subprocess
import time
import sys
from alsaaudio import Mixer
from threading import Thread, Timer

import serial

import mycroft.dialog
from mycroft.client.enclosure.base import Enclosure
from mycroft.api import has_been_paired
from mycroft.audio import wait_while_speaking
from mycroft.client.enclosure.russell.arduino import EnclosureArduino
#from mycroft.client.enclosure.mark1.eyes import EnclosureEyes
#from mycroft.client.enclosure.mark1.mouth import EnclosureMouth
from mycroft.enclosure.display_manager import \
    init_display_manager_bus_connection
from mycroft.configuration import Configuration, LocalConf, USER_CONFIG
from mycroft.messagebus.message import Message
from mycroft.util import play_wav, create_signal, connected, check_for_signal
from mycroft.util.audio_test import record
from mycroft.util.log import LOG
from queue import Queue

# Enclosure File Derived from the Mark 1
# From the original file:
# The Mark 1 hardware consists of a Raspberry Pi main CPU which is connected
# to an Arduino over the serial port.  A custom serial protocol sends
# commands to control various visual elements which are controlled by the
# Arduino (e.g. two circular rings of RGB LEDs; and four 8x8 white LEDs).
#
# The Arduino can also send back notifications in response to either
# pressing or turning a rotary encoder.


class EnclosureReader(Thread):
    """
    Reads data from Serial port.

    Listens to all commands sent by Arduino that must be be performed on
    Mycroft Core.

    E.g. Mycroft Stop Feature
        #. Arduino sends a Stop command after a button press on a Mycroft unit
        #. ``EnclosureReader`` captures the Stop command
        #. Notify all Mycroft Core processes (e.g. skills) to be stopped

    Note: A command is identified by a line break
    """

    def __init__(self, serial, bus, lang=None):
        super(EnclosureReader, self).__init__(target=self.read)
        self.alive = True
        self.daemon = True
        self.serial = serial
        self.bus = bus
        self.lang = lang or 'en-us'
        self.start()

        # Notifications from mycroft-core
        self.bus.on("mycroft.stop.handled", self.on_stop_handled)

    def read(self):
        while self.alive:
            try:
                data = self.serial.readline()[:-2]
                if data:
                    try:
                        data_str = data.decode()
                    except UnicodeError as e:
                        data_str = data.decode('utf-8', errors='replace')
                        LOG.warning('Invalid characters in response from '
                                    ' enclosure: {}'.format(repr(e)))
                    self.process(data_str)
            except Exception as e:
                LOG.error("Reading error: {0}".format(e))

    def on_stop_handled(self, event):
        # A skill performed a stop
        check_for_signal('buttonPress')

    def process(self, data):
        # TODO: Look into removing this emit altogether.
        # We need to check if any other serial bus messages
        # are handled by other parts of the code
        if "mycroft.stop" not in data:
            self.bus.emit(Message(data))

        if "Command: system.version" in data:
            # This happens in response to the "system.version" message
            # sent during the construction of Enclosure()
            self.bus.emit(Message("enclosure.started"))

        if "mycroft.stop" in data:
            if has_been_paired():
                create_signal('buttonPress')
                self.bus.emit(Message("mycroft.stop"))

        if "volume.up" in data:
            self.bus.emit(Message("mycroft.volume.increase",
                                  {'play_sound': True}))

        if "volume.down" in data:
            self.bus.emit(Message("mycroft.volume.decrease",
                                  {'play_sound': True}))

        if "system.test.begin" in data:
            self.bus.emit(Message('recognizer_loop:sleep'))

        if "system.test.end" in data:
            self.bus.emit(Message('recognizer_loop:wake_up'))

        if "mic.test" in data:
            mixer = Mixer()
            prev_vol = mixer.getvolume()[0]
            mixer.setvolume(35)
            self.bus.emit(Message("speak", {
                'utterance': "I am testing one two three"}))

            time.sleep(0.5)  # Prevents recording the loud button press
            record("/tmp/test.wav", 3.0)
            mixer.setvolume(prev_vol)
            play_wav("/tmp/test.wav").communicate()

            # Test audio muting on arduino
            subprocess.call('speaker-test -P 10 -l 0 -s 1', shell=True)

        if "unit.shutdown" in data:
            # Eyes to soft gray on shutdown
#            self.bus.emit(Message("enclosure.eyes.color",
#                                  {'r': 70, 'g': 65, 'b': 69}))
#            self.bus.emit(
#                Message("enclosure.eyes.timedspin",
#                        {'length': 12000}))
#            self.bus.emit(Message("enclosure.mouth.reset"))
            time.sleep(0.5)  # give the system time to pass the message
            self.bus.emit(Message("system.shutdown"))

        if "unit.reboot" in data:
            # Eyes to soft gray on reboot
#            self.bus.emit(Message("enclosure.eyes.color",
#                                  {'r': 70, 'g': 65, 'b': 69}))
#            self.bus.emit(Message("enclosure.eyes.spin"))
#            self.bus.emit(Message("enclosure.mouth.reset"))
            time.sleep(0.5)  # give the system time to pass the message
            self.bus.emit(Message("system.reboot"))

        if "unit.setwifi" in data:
            self.bus.emit(Message("system.wifi.setup", {'lang': self.lang}))

        if "unit.factory-reset" in data:
            self.bus.emit(Message("speak", {
                'utterance': mycroft.dialog.get("reset to factory defaults")}))
            subprocess.call(
                'rm ~/.mycroft/identity/identity2.json',
                shell=True)
            self.bus.emit(Message("system.wifi.reset"))
            self.bus.emit(Message("system.ssh.disable"))
            wait_while_speaking()
#            self.bus.emit(Message("enclosure.mouth.reset"))
#            self.bus.emit(Message("enclosure.eyes.spin"))
#            self.bus.emit(Message("enclosure.mouth.reset"))
            time.sleep(5)  # give the system time to process all messages
            self.bus.emit(Message("system.reboot"))

        if "unit.enable-ssh" in data:
            # This is handled by the wifi client
            self.bus.emit(Message("system.ssh.enable"))
            self.bus.emit(Message("speak", {
                'utterance': mycroft.dialog.get("ssh enabled")}))

        if "unit.disable-ssh" in data:
            # This is handled by the wifi client
            self.bus.emit(Message("system.ssh.disable"))
            self.bus.emit(Message("speak", {
                'utterance': mycroft.dialog.get("ssh disabled")}))

        if "unit.enable-learning" in data or "unit.disable-learning" in data:
            enable = 'enable' in data
            word = 'enabled' if enable else 'disabled'

            LOG.info("Setting opt_in to: " + word)
            new_config = {'opt_in': enable}
            user_config = LocalConf(USER_CONFIG)
            user_config.merge(new_config)
            user_config.store()

            self.bus.emit(Message("speak", {
                'utterance': mycroft.dialog.get("learning " + word)}))

    def stop(self):
        self.alive = False


class EnclosureWriter(Thread):
    """
    Writes data to Serial port.
        #. Enqueues all commands received from Mycroft enclosures
           implementation
        #. Process them on the received order by writing on the Serial port

    E.g. Displaying a text on Mycroft's Mouth
        #. ``EnclosureMouth`` sends a text command
        #. ``EnclosureWriter`` captures and enqueue the command
        #. ``EnclosureWriter`` removes the next command from the queue
        #. ``EnclosureWriter`` writes the command to Serial port

    Note: A command has to end with a line break
    """

    def __init__(self, serial, bus, size=16):
        super(EnclosureWriter, self).__init__(target=self.flush)
        self.alive = True
        self.daemon = True
        self.serial = serial
        self.bus = bus
        self.commands = Queue(size)
        self.start()

    def flush(self):
        while self.alive:
            try:
                cmd = self.commands.get() + '\n'
                self.serial.write(cmd.encode())
                self.commands.task_done()
            except Exception as e:
                LOG.error("Writing error: {0}".format(e))

    def write(self, command):
        self.commands.put(str(command))

    def stop(self):
        self.alive = False

class EnclosureRussell(Enclosure):
    """
    From the original Mark1 readme:
    Serves as a communication interface between Arduino and Mycroft Core.

    ``Enclosure`` initializes and aggregates all enclosures implementation.

    E.g. ``EnclosureEyes``, ``EnclosureMouth`` and ``EnclosureArduino``

    It also listens to the basic events in order to perform those core actions
    on the unit.

    E.g. Start and Stop talk animation
    """