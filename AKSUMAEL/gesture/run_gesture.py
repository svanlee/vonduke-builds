#!/usr/bin/env python3
"""
gesture/run_gesture.py — Standalone gesture control runner.

Run from the AKSUMAEL root:
    python3 gesture/run_gesture.py                     # camera 0, display off
    python3 gesture/run_gesture.py --display            # show annotated feed
    python3 gesture/run_gesture.py --cam 1 --no-bot    # camera 1, no AKSUMAEL injection
    python3 gesture/run_gesture.py --target 192.168.0.202:7700  # add UDP target

Gestures:
    Open palm  → STOP (bot stops, RoboCar stops)
    Thumbs up  → FORWARD (bot explores, RoboCar goes forward)
    Fist       → HOLD (bot clears goals, RoboCar holds position)
    Point left → TURN_LEFT (RoboCar turns left, no bot goal)
    Point right→ TURN_RIGHT (RoboCar turns right, no bot goal)

Press Q to quit if --display is active.
"""

import argparse
import signal
import sys

def main():
    parser = argparse.ArgumentParser(description='Gesture control layer')
    parser.add_argument('--cam', type=int, default=0, help='Camera index (default 0)')
    parser.add_argument('--display', action='store_true', help='Show annotated video feed')
    parser.add_argument('--no-bot', action='store_true', help='Disable AKSUMAEL goal injection')
    parser.add_argument('--target', action='append', metavar='HOST:PORT',
                        help='Add UDP target (can specify multiple times)')
    args = parser.parse_args()

    # Parse UDP targets
    targets = []
    if args.target:
        for t in args.target:
            host, port = t.rsplit(':', 1)
            targets.append((host, int(port)))

    from gesture.recognizer import GestureRecognizer, GestureCommand
    from gesture.dispatcher import GestureDispatcher, DEFAULT_TARGETS

    udp_targets = targets if targets else DEFAULT_TARGETS

    rec = GestureRecognizer(camera_index=args.cam, display=args.display)
    dis = GestureDispatcher(
        targets=udp_targets,
        aksumael=not args.no_bot,
        verbose=True,
    )

    # Graceful shutdown
    def _shutdown(sig, frame):
        print('\n[GESTURE] shutting down')
        rec.close()
        dis.close()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    print(f'[GESTURE] running — camera {args.cam}, targets: {udp_targets}')
    print('[GESTURE] gestures: open palm=STOP  thumbs-up=FORWARD  fist=HOLD  point L/R=TURN')

    for result in rec.stream():
        dis.dispatch(result.command)


if __name__ == '__main__':
    main()
