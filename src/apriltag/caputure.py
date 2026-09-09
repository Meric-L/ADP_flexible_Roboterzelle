from picamera2 import Picamera2
import cv2, time

cam = Picamera2()
cam.configure(cam.create_still_configuration(main={"size": (2028, 1520)}))
cam.start(); time.sleep(2)
img = cv2.cvtColor(cam.capture_array(), cv2.COLOR_RGB2BGR)
cam.stop(); cam.close()

par = cv2.aruco.DetectorParameters()
par.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_APRILTAG
det = cv2.aruco.ArucoDetector(
    cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11), par)

corners, ids, _ = det.detectMarkers(img)
print("Gefunden:", ids.ravel().tolist() if ids is not None else "nichts")

if ids is not None:
    cv2.aruco.drawDetectedMarkers(img, corners, ids)
cv2.imwrite("detect_debug.jpg", img)
