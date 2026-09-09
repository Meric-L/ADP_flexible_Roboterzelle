from picamera2 import Picamera2
import cv2, time

TIMEOUT_S = 120


def scan_qr_code(timeout=TIMEOUT_S):
    cam = Picamera2()
    cam.configure(cam.create_video_configuration(
        main={"size": (2028, 1520)}, controls={"FrameRate": 30}))
    cam.start()
    time.sleep(2)

    detector = cv2.QRCodeDetector()
    start = time.time()

    try:
        while time.time() - start < timeout:
            frame = cv2.cvtColor(cam.capture_array(), cv2.COLOR_RGB2BGR)
            data, points, _ = detector.detectAndDecode(frame)

            cv2.imshow("QR Scanner", frame)
            cv2.waitKey(200)

            if data:
                return data
        return None
    finally:
        cam.stop()
        cam.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    result = scan_qr_code()
    if result:
        print("QR-Code Inhalt:", result)
    else:
        print("return statement nicht gefunden")
