import cv2
import subprocess

def detect_camera():
    """Try capture devices 0-5, return first working index."""
    for i in range(6):
        cap = cv2.VideoCapture(i)
        if cap.isOpened():
            ret, _ = cap.read()
            cap.release()
            if ret:
                print(f'[HW] camera found at index {i}')
                return i
    print('[HW] no camera found, defaulting to 0')
    return 0

def detect_mic():
    """Return first input device index via pyaudio."""
    try:
        import pyaudio
        pa = pyaudio.PyAudio()
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info['maxInputChannels'] > 0:
                print(f"[HW] mic found: {info['name']} (index {i})")
                pa.terminate()
                return i
        pa.terminate()
    except Exception as e:
        print(f'[HW] mic detection failed: {e}')
    return None

def list_video_devices():
    """List /dev/video* devices on Linux."""
    try:
        result = subprocess.run(['v4l2-ctl', '--list-devices'],
                                capture_output=True, text=True)
        print('[HW] video devices:\n', result.stdout)
    except FileNotFoundError:
        print('[HW] v4l2-ctl not found (install v4l-utils)')

if __name__ == '__main__':
    list_video_devices()
    print('camera:', detect_camera())
    print('mic:', detect_mic())
