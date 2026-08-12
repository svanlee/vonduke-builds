# gesture — MediaPipe hand gesture control layer
# Recognizes: STOP (palm up), FORWARD (thumbs up), HOLD (fist),
#             TURN_LEFT (point left), TURN_RIGHT (point right)
# Output: sends command strings over UDP to configurable targets
#         (AKSUMAEL goal injector, RoboCar, hive nodes)

from gesture.recognizer import GestureRecognizer, GestureCommand
from gesture.dispatcher import GestureDispatcher

__all__ = ['GestureRecognizer', 'GestureCommand', 'GestureDispatcher']
