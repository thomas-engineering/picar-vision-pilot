#!/usr/bin/env python3
"""Record a test clip from the car's webcam, for tuning the follow-mode tracker offline.

Run it on the Pi (python3-opencv from apt is enough):

    python3 tools/record_clip.py NAME [--seconds 25] [--size 320x240] [--fps 15] [--countdown 5] [--preview PORT [--steps FILE]]

NAME is a short label such as bright-ball or grey-box. The frames are kept in memory while
recording, so the slow SD card cannot drop any, and are written to clips/NAME/ afterwards as
JPEG files (quality 95) with a meta.json. Copy them to the PC with:

    scp -r picar:video_car/clips/NAME .

Close the control page in every browser first: the server only releases the camera when no
viewer is left. With --preview PORT the script shows the camera itself at http://<car>:PORT/, with
the centre and the Learn box drawn in, and only starts the countdown when Start is pressed there.
--steps FILE (see tools/clip_steps/) shows under the picture what to do at each second of the
recording, and the steps are saved in meta.json, so the frames can be labelled by time. Move the object slowly through the picture, closer and farther, partly behind
something once, with other things in view.
"""
import argparse
import http.server
import json
import os
import sys
import threading
import time

import cv2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREVIEW_EVERY = 3  # frames between preview pictures, so the preview costs the recording little
RETICLE = 0.3  # side of the Learn box as a share of the image height, as in server/follow.py

PAGE = b"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Clip preview</title><style>body{font:16px sans-serif;background:#111;color:#eee;margin:16px;text-align:center}
img{width:100%;max-width:640px;image-rendering:pixelated}button{font-size:20px;padding:10px 30px;margin:10px}</style>
</head><body><img src="/stream"><div><button onclick="fetch('/start',{method:'POST'})">Start</button></div>
<div id="s"></div><div id="now" style="font-size:24px;font-weight:bold;margin:12px 0;color:#ffd84d"></div>
<div id="next" style="color:#aaa"></div><script>setInterval(()=>fetch('/state').then(r=>r.json()).then(j=>{
s.textContent=j.state; now.textContent=j.now; next.textContent=j.next?'Next: '+j.next:''}).catch(()=>{}),300)</script>
</body></html>"""


class Preview:
    """The camera picture for a browser while positioning and recording, and the Start button."""

    def __init__(self, port):
        self.jpeg, self.state = None, "position the object, then press Start"
        self.now, self.next = "", ""
        self.start = threading.Event()
        self.changed = threading.Condition()
        preview = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == "/stream":
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                    self.end_headers()
                    try:
                        while True:
                            with preview.changed:
                                preview.changed.wait(2)
                                data = preview.jpeg
                            if data:
                                self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + data + b"\r\n")
                    except (BrokenPipeError, ConnectionResetError):
                        return
                state = json.dumps({"state": preview.state, "now": preview.now, "next": preview.next}).encode()
                body = PAGE if self.path == "/" else state if self.path == "/state" else None
                if body is None:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8" if self.path == "/" else "application/json")
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                if self.path == "/start":
                    preview.start.set()
                self.send_response(204)
                self.end_headers()

        server = http.server.ThreadingHTTPServer(("", port), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()

    def follow_steps(self, steps, second):
        """Show the step due at this second of the recording, the next one and how long the current one lasts."""
        due = [i for i, (start, _) in enumerate(steps) if start <= second]
        if not due:
            self.now, self.next = "Get ready: " + steps[0][1], ""
            return
        i = due[-1]
        if i + 1 < len(steps):
            self.now = "%s (%.0f s)" % (steps[i][1], steps[i + 1][0] - second)
            self.next = steps[i + 1][1]
        else:
            self.now, self.next = steps[i][1], ""

    def show(self, image, label):
        """Publish a frame with the centre, the Learn box and label drawn on a copy."""
        view = image.copy()
        h, w = view.shape[:2]
        side = int(RETICLE * h)
        cv2.rectangle(view, ((w - side) // 2, (h - side) // 2), ((w + side) // 2, (h + side) // 2), (0, 255, 0), 1)
        cv2.drawMarker(view, (w // 2, h // 2), (0, 255, 0), cv2.MARKER_CROSS, 12, 1)
        cv2.putText(view, label, (6, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 1, cv2.LINE_AA)
        ok, jpg = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            with self.changed:
                self.jpeg = jpg.tobytes()
                self.changed.notify_all()


def read_steps(path):
    """(second, instruction) pairs from a steps file: one per line, '#' starts a comment."""
    steps = []
    with open(path) as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                second, text = line.split(None, 1)
                steps.append((float(second), text))
    if not steps or steps != sorted(steps, key=lambda s: s[0]):
        sys.exit("%s: needs lines 'SECOND instruction' in time order" % path)
    return steps


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("name")
    parser.add_argument("--seconds", type=float, default=25)
    parser.add_argument("--size", default="320x240", help="WIDTHxHEIGHT, like the car (default 320x240)")
    parser.add_argument("--fps", type=int, default=15)
    parser.add_argument("--countdown", type=int, default=5, help="seconds before recording starts")
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--preview", type=int, default=0, metavar="PORT",
                        help="show the camera in a browser on this port and wait for Start there")
    parser.add_argument("--steps", metavar="FILE", help="instructions by second, shown under the preview")
    args = parser.parse_args()
    if not args.name or "/" in args.name or args.name.startswith("."):
        sys.exit("NAME must be a plain label")
    steps = read_steps(args.steps) if args.steps else []
    if steps and not args.preview:
        sys.exit("--steps needs --preview")
    width, height = (int(v) for v in args.size.split("x"))
    out = os.path.join(ROOT, "clips", args.name)
    if os.path.exists(out) and os.listdir(out):
        sys.exit("%s is not empty; pick another name" % out)

    cap = cv2.VideoCapture(args.device, cv2.CAP_V4L2) if args.device.startswith("/dev/") else cv2.VideoCapture(args.device)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
    cap.set(cv2.CAP_PROP_FPS, args.fps)
    cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if not cap.isOpened():
        sys.exit("cannot open %s. Is the control page still open in a browser?" % args.device)
    for _ in range(10):  # let the automatic exposure settle
        cap.read()

    preview = Preview(args.preview) if args.preview else None
    if preview and steps:
        preview.follow_steps(steps, -1)
    if preview:
        print("preview at http://%s:%d/ ; press Start there" % (os.uname().nodename, args.preview), flush=True)
        while not preview.start.is_set():
            ok, image = cap.read()
            if ok:
                preview.show(image, "position, then Start")

    for left in range(args.countdown, 0, -1):
        print("starting in %d ..." % left, flush=True)
        if preview:
            preview.state = "starting in %d" % left
        end = time.monotonic() + 1
        while time.monotonic() < end:
            ok, image = cap.read()  # keep draining so the first recorded frame is fresh
            if ok and preview:
                preview.show(image, "starting in %d" % left)
    print("recording %.0f s" % args.seconds, flush=True)

    frames, stamps = [], []
    start = time.monotonic()
    while time.monotonic() - start < args.seconds:
        ok, image = cap.read()
        if not ok:
            print("frame read failed", file=sys.stderr)
            break
        stamps.append(time.monotonic() - start)
        ok, jpg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if ok:
            frames.append(jpg.tobytes())
        if preview and len(frames) % PREVIEW_EVERY == 0:
            left = args.seconds - stamps[-1]
            preview.state = "recording, %.0f s left" % left
            if steps:
                preview.follow_steps(steps, stamps[-1])
            preview.show(image, "REC %.0f s" % left)
    cap.release()
    if preview:
        preview.state = "saving ..."
        preview.now, preview.next = "", ""
    if not frames:
        sys.exit("no frames recorded")

    os.makedirs(out, exist_ok=True)
    for i, data in enumerate(frames):
        with open(os.path.join(out, "%05d.jpg" % i), "wb") as f:
            f.write(data)
    duration = stamps[-1]
    meta = {
        "name": args.name,
        "frames": len(frames),
        "width": image.shape[1],
        "height": image.shape[0],
        "seconds": round(duration, 2),
        "fps": round((len(frames) - 1) / duration, 1) if duration else None,
        "timestamps": [round(t, 3) for t in stamps],
    }
    if steps:
        meta["steps"] = [{"second": second, "do": text} for second, text in steps]
    with open(os.path.join(out, "meta.json"), "w") as f:
        json.dump(meta, f)
    print("%d frames, %.1f fps, %dx%d, saved to %s" % (len(frames), meta["fps"] or 0, meta["width"], meta["height"], out))
    if preview:
        preview.state = "saved %d frames, done" % len(frames)
        time.sleep(1.5)  # let the page show it


main()
