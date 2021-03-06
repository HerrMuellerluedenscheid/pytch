import unittest
from subprocess import call
from pytch.data import MicrophoneRecorder
import time


class MicTestCase(unittest.TestCase):
    def test_micInit(self):

        # kill pulseaudio to check proper startup
        try:
            call(["pulseaudio", "--kill"])
        except OSError as e:
            if e.errno == 2:
                pass
            else:
                raise e
        mic = MicrophoneRecorder()
        # mic.device_no = 7
        mic.start_new_stream()
        mic.terminate()

    def test_zoom(self):
        """ works with the zoom interface."""
        mic = MicrophoneRecorder(chunksize=512, sampling_rate=44100, nchannels=16)
        mic.device_no = 5
        p = mic.p
        host_device_count = p.get_device_count()
        mic.start()
        time.sleep(1)
        mic.stop()


if __name__ == "__main__":
    unittest.main()
