import cv2 as cv
import time
import sys
import argparse
import threading
import numpy as np

# Global variables for thread communication
frame_buffer = None
running = True
new_frame_available = False
buffer_lock = threading.Lock()

def capture_thread(url):
    global frame_buffer, running, new_frame_available
    
    cap = None
    print(f"[{time.strftime('%X')}] Capture thread started for {url}")

    while running:
        try:
            if cap is None or not cap.isOpened():
                cap = cv.VideoCapture(url)
                if not cap.isOpened():
                    time.sleep(2)
                    continue
                print(f"[{time.strftime('%X')}] Connected to stream!")
            
            ret, frame = cap.read()
            if ret:
                with buffer_lock:
                    frame_buffer = frame
                    new_frame_available = True
            else:
                print(f"[{time.strftime('%X')}] Stream disconnected")
                cap.release()
                cap = None
                time.sleep(1)
                
        except Exception as e:
            print(f"Capture error: {e}")
            time.sleep(1)

    if cap:
        cap.release()
    print("Capture thread stopped")

def main():
    global running, new_frame_available, frame_buffer
    
    parser = argparse.ArgumentParser(description='Flight Streamer Client')
    parser.add_argument('ip', nargs='?', default='192.168.4.1', help='IP address of the ESP32 (default: 192.168.4.1)')
    parser.add_argument('--port', type=int, default=81, help='Stream port (default: 81)')
    args = parser.parse_args()

    url = f'http://{args.ip}:{args.port}/stream'
    window_name = f'Flight Streamer - {args.ip}'
    
    print(f"Target URL: {url}")
    print("Press 'q' to quit")

    # Call startWindowThread to ensure macOS can handle window events properly
    cv.startWindowThread()

    # Create window and show placeholder immediately
    cv.namedWindow(window_name, cv.WINDOW_NORMAL)
    dummy_frame = np.zeros((240, 320, 3), dtype=np.uint8)
    cv.putText(dummy_frame, "Connecting...", (80, 120), cv.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    cv.imshow(window_name, dummy_frame)
    
    # Force a few event loop cycles to ensure window renders
    for _ in range(5):
        cv.waitKey(10)

    # Start capture thread
    t = threading.Thread(target=capture_thread, args=(url,), daemon=True)
    t.start()

    # GUI Loop
    try:
        while running:
            if new_frame_available:
                with buffer_lock:
                    disp_frame = frame_buffer.copy()
                    new_frame_available = False
                
                cv.imshow(window_name, disp_frame)
            
            # Pump events even if no new frame
            # waitKey(1) allows for faster UI response
            key = cv.waitKey(1) & 0xFF
            if key == ord('q'):
                running = False
                break

            # Check close button
            try:
                # On some systems, checking visibility is the only way to detect the 'X' button
                if cv.getWindowProperty(window_name, cv.WND_PROP_VISIBLE) < 1:
                    running = False
                    break
            except:
                pass
    except KeyboardInterrupt:
        print("\nInterrupted by user")
            
    running = False
    
    # Clean exit
    cv.destroyAllWindows()
    # Ensure window is gone before joining thread
    for _ in range(5):
        cv.waitKey(1)
        
    t.join(timeout=1.0)
    print('\nVideo stopped')

if __name__ == '__main__':
    main()
