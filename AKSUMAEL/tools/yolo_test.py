import cv2
from ultralytics import YOLO

# Grab one frame from the capture card
cap = cv2.VideoCapture("/dev/video0")  # or /dev/video1
ret, frame = cap.read()
cap.release()

if not ret:
    print("Failed to grab frame — try /dev/video1")
else:
    cv2.imwrite("test_frame.jpg", frame)
    print("Saved test_frame.jpg", frame.shape)

    # Load a small pretrained model (downloads ~6MB on first run)
    model = YOLO("yolov8n.pt")
    results = model("test_frame.jpg")

    for r in results:
        print(r.boxes)  # detected boxes, classes, confidences
        r.save(filename="test_frame_annotated.jpg")