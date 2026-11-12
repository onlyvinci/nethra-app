"""
Nethra — Offline Eye Comfort Assistant for Laptops and PCs.
Native Desktop Application (Tkinter + OpenCV + Pillow).
"""

import sys
import time
import json
import subprocess
from pathlib import Path
from datetime import datetime
import threading
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

# These are required runtime dependencies. The launcher installs them before startup.
# Importing them directly also lets Pylance understand their APIs correctly.
import cv2
from PIL import Image, ImageTk
import pyttsx3

ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"

THEME = {
    "bg": "#f4f1ea",
    "card": "#ffffff",
    "border": "#e3ded6",
    "ink": "#1e2221",
    "muted": "#69706d",
    "accent": "#1f4f46",
    "accent_hover": "#2a665a",
    "gold": "#c2a373",
    "badge_on": "#e7f3ec",
    "badge_on_ink": "#235c44",
    "badge_off": "#ebe8e1",
    "badge_off_ink": "#69706d",
    "camera_bg": "#151817",
}


def get_cascade_classifier(xml_path):
    """Create and validate an OpenCV Haar cascade classifier."""
    if cv2 is None:
        raise RuntimeError("OpenCV is not installed. Run: python -m pip install opencv-python")
    classifier_factory = getattr(cv2, "CascadeClassifier", None)
    if classifier_factory is None:
        nested_cv2 = getattr(cv2, "cv2", None)
        classifier_factory = getattr(nested_cv2, "CascadeClassifier", None)
    if classifier_factory is None:
        raise RuntimeError(
            "The installed cv2 package is not the correct OpenCV build. "
            "In the VS Code terminal run:\n"
            "python -m pip uninstall opencv-python opencv-contrib-python opencv-python-headless -y\n"
            "python -m pip install opencv-python"
        )
    classifier = classifier_factory(str(xml_path))
    if classifier.empty():
        raise RuntimeError(f"Could not load face/eye detector file: {xml_path}")
    return classifier


def send_windows_push_notification(title: str, message: str):
    """Sends native Windows Action Center banner notification."""
    if sys.platform != "win32":
        return

    def _worker():
        try:
            clean_title = title.replace('"', '`"')
            clean_msg = message.replace('"', '`"')
            ps_script = f"""
            [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
            $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
            $textNodes = $template.GetElementsByTagName("text")
            $textNodes.Item(0).AppendChild($template.CreateTextNode("{clean_title}")) > $null
            $textNodes.Item(1).AppendChild($template.CreateTextNode("{clean_msg}")) > $null
            $notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("Nethra Eye Comfort")
            $notification = [Windows.UI.Notifications.ToastNotification]::new($template)
            $notifier.Show($notification)
            """
            subprocess.run(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
                creationflags=0x08000000,
                timeout=3,
            )
        except Exception:
            pass

    threading.Thread(target=_worker, daemon=True).start()


def apply_windows_warmth(enabled: bool, level: int = 25):
    """Adjusts display gamma ramp via Windows GDI ctypes."""
    if sys.platform != "win32":
        return False, "Screen warmth adjustment is only supported on Windows."
    import ctypes

    gdi32 = ctypes.windll.gdi32
    user32 = ctypes.windll.user32
    hdc = user32.GetDC(None)
    if not hdc:
        return False, "Could not acquire Windows display device context."

    try:
        ramp_type = (ctypes.c_ushort * 256) * 3
        ramp = ramp_type()
        if enabled:
            warmth = max(0, min(100, int(level))) / 100.0
            red_gain = 1.0
            green_gain = 1.0 - 0.035 * warmth
            blue_gain = 1.0 - 0.19 * warmth
            for i in range(256):
                ramp[0][i] = int(min(65535, i * 257 * red_gain))
                ramp[1][i] = int(min(65535, i * 257 * green_gain))
                ramp[2][i] = int(min(65535, i * 257 * blue_gain))
        else:
            for i in range(256):
                val = i * 257
                ramp[0][i] = val
                ramp[1][i] = val
                ramp[2][i] = val

        ok = bool(gdi32.SetDeviceGammaRamp(hdc, ctypes.byref(ramp)))
        if ok:
            return True, "Warmth filter applied." if enabled else "Display neutral color restored."
        return False, "Display driver rejected gamma adjustment. Use Windows Night Light."
    except Exception as exc:
        return False, f"Gamma error: {exc}"
    finally:
        user32.ReleaseDC(None, hdc)


class SpeechWorker:
    def __init__(self):
        self._queue = []
        self._lock = threading.Lock()
        self._thread = None

    def speak(self, text: str):
        if not pyttsx3 or not text:
            return
        with self._lock:
            self._queue.append(text)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._process, daemon=True)
                self._thread.start()

    def _process(self):
        try:
            engine = pyttsx3.init()
            engine.setProperty("rate", 165)
            engine.setProperty("volume", 0.95)
            while True:
                with self._lock:
                    if not self._queue:
                        break
                    phrase = self._queue.pop(0)
                engine.say(phrase)
                engine.runAndWait()
        except Exception:
            pass


class NethraDesktopApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Nethra — Eye Comfort Assistant")
        self.geometry("1140x840")
        self.minsize(1000, 740)
        self.configure(bg=THEME["bg"])

        self.session_running = False
        self.session_seconds = 0
        self.total_reminders = 0

        self.voice_enabled = tk.BooleanVar(value=True)
        self.push_enabled = tk.BooleanVar(value=True)
        self.blink_reminders_enabled = tk.BooleanVar(value=True)
        self.posture_reminders_enabled = tk.BooleanVar(value=True)
        self.warmth_enabled = tk.BooleanVar(value=False)
        self.warmth_level = tk.IntVar(value=25)

        self.camera_running = False
        self.camera_thread = None
        self.latest_frame = None
        self.frame_lock = threading.Lock()
        self.face_detected = False
        self.eyes_detected_count = 0
        self.blinks = 0

        self.no_eye_frame_count = 0
        self.last_eye_present = True

        self.speaker = SpeechWorker()
        self._configure_styles()
        self._build_header()
        self._build_hero()
        self._build_metrics_row()
        self._build_main_split()
        self._build_footer()

        self.after(1000, self._session_tick)
        self.after(25, self._render_camera_feed)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", background=THEME["bg"], foreground=THEME["ink"], font=("Segoe UI", 10))
        style.configure(
            "Primary.TButton",
            background=THEME["gold"],
            foreground="#221e17",
            font=("Segoe UI", 10, "bold"),
            borderwidth=0,
            padding=8,
        )
        style.map("Primary.TButton", background=[("active", "#b89762")])
        style.configure(
            "Ghost.TButton",
            background=THEME["accent"],
            foreground="#ffffff",
            font=("Segoe UI", 10, "bold"),
            borderwidth=1,
            padding=8,
        )
        style.map("Ghost.TButton", background=[("active", THEME["accent_hover"])])
        style.configure(
            "Action.TButton",
            background=THEME["card"],
            foreground=THEME["ink"],
            font=("Segoe UI", 9, "bold"),
            relief="solid",
            borderwidth=1,
            padding=6,
        )

    def _build_header(self):
        header = tk.Frame(self, bg=THEME["bg"])
        header.pack(fill=tk.X, padx=32, pady=(18, 10))

        brand = tk.Frame(header, bg=THEME["bg"])
        brand.pack(side=tk.LEFT)

        logo = tk.Label(brand, text="N", font=("Segoe UI", 16, "bold"), bg=THEME["accent"], fg="#fff", width=3, height=1)
        logo.pack(side=tk.LEFT, padx=(0, 12))

        tb = tk.Frame(brand, bg=THEME["bg"])
        tb.pack(side=tk.LEFT)
        tk.Label(tb, text="Nethra", font=("Segoe UI", 16, "bold"), fg=THEME["ink"], bg=THEME["bg"]).pack(anchor="w")
        tk.Label(tb, text="Real-Time Native Desktop Companion", font=("Segoe UI", 9), fg=THEME["muted"], bg=THEME["bg"]).pack(anchor="w")

        self.status_badge = tk.Label(
            header, text="● Offline Engine Ready", font=("Segoe UI", 9, "bold"), bg=THEME["badge_on"], fg=THEME["badge_on_ink"], padx=14, pady=6
        )
        self.status_badge.pack(side=tk.RIGHT)

    def _build_hero(self):
        hero = tk.Frame(self, bg=THEME["accent"], padx=24, pady=18)
        hero.pack(fill=tk.X, padx=32, pady=(0, 16))

        txt = tk.Frame(hero, bg=THEME["accent"])
        txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        tk.Label(txt, text="Look after your eyes while you work.", font=("Segoe UI", 14, "bold"), fg="#fff", bg=THEME["accent"]).pack(anchor="w")
        tk.Label(
            txt,
            text="Real-time optical eye tracking, native Windows push notifications, and display comfort.",
            font=("Segoe UI", 9),
            fg="#d0ded9",
            bg=THEME["accent"],
        ).pack(anchor="w", pady=(2, 0))

        actions = tk.Frame(hero, bg=THEME["accent"])
        actions.pack(side=tk.RIGHT)

        self.session_btn = ttk.Button(actions, text="Start Session", style="Primary.TButton", command=self.toggle_session)
        self.session_btn.pack(side=tk.LEFT, padx=6)

        test_btn = ttk.Button(
            actions, text="Test Push Alert", style="Ghost.TButton", command=lambda: self.trigger_notification("Nethra Test", "Push notifications and speech are active.")
        )
        test_btn.pack(side=tk.LEFT, padx=6)

    def _build_metrics_row(self):
        mf = tk.Frame(self, bg=THEME["bg"])
        mf.pack(fill=tk.X, padx=32, pady=(0, 16))
        for col in range(3):
            mf.columnconfigure(col, weight=1)

        c1 = tk.Frame(mf, bg=THEME["card"], relief="solid", bd=1, padx=16, pady=12)
        c1.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        tk.Label(c1, text="Session Time", font=("Segoe UI", 9), fg=THEME["muted"], bg=THEME["card"]).pack(anchor="w")
        self.session_label = tk.Label(c1, text="00:00", font=("Segoe UI", 20, "bold"), fg=THEME["ink"], bg=THEME["card"])
        self.session_label.pack(anchor="w")
        tk.Label(c1, text="Active work session duration", font=("Segoe UI", 8), fg=THEME["muted"], bg=THEME["card"]).pack(anchor="w")

        c2 = tk.Frame(mf, bg=THEME["card"], relief="solid", bd=1, padx=16, pady=12)
        c2.grid(row=0, column=1, sticky="nsew", padx=4)
        tk.Label(c2, text="Verified Blinks", font=("Segoe UI", 9), fg=THEME["muted"], bg=THEME["card"]).pack(anchor="w")
        self.blink_metric_label = tk.Label(c2, text="0", font=("Segoe UI", 20, "bold"), fg=THEME["ink"], bg=THEME["card"])
        self.blink_metric_label.pack(anchor="w")
        self.blink_hint_label = tk.Label(c2, text="Start camera to track blinks", font=("Segoe UI", 8), fg=THEME["muted"], bg=THEME["card"])
        self.blink_hint_label.pack(anchor="w")

        c3 = tk.Frame(mf, bg=THEME["card"], relief="solid", bd=1, padx=16, pady=12)
        c3.grid(row=0, column=2, sticky="nsew", padx=(8, 0))
        tk.Label(c3, text="Comfort Index", font=("Segoe UI", 9), fg=THEME["muted"], bg=THEME["card"]).pack(anchor="w")
        self.comfort_score_label = tk.Label(c3, text="100", font=("Segoe UI", 20, "bold"), fg=THEME["ink"], bg=THEME["card"])
        self.comfort_score_label.pack(anchor="w")
        tk.Label(c3, text="Habit health score (not clinical)", font=("Segoe UI", 8), fg=THEME["muted"], bg=THEME["card"]).pack(anchor="w")

    def _build_main_split(self):
        container = tk.Frame(self, bg=THEME["bg"])
        container.pack(fill=tk.BOTH, expand=True, padx=32, pady=(0, 10))
        container.columnconfigure(0, weight=6)
        container.columnconfigure(1, weight=5)

        left_col = tk.Frame(container, bg=THEME["card"], relief="solid", bd=1, padx=16, pady=16)
        left_col.grid(row=0, column=0, sticky="nsew", padx=(0, 8))

        cam_head = tk.Frame(left_col, bg=THEME["card"])
        cam_head.pack(fill=tk.X)
        tk.Label(cam_head, text="Camera Monitor", font=("Segoe UI", 11, "bold"), fg=THEME["ink"], bg=THEME["card"]).pack(side=tk.LEFT)
        self.cam_badge = tk.Label(cam_head, text="Off", font=("Segoe UI", 8, "bold"), bg=THEME["badge_off"], fg=THEME["badge_off_ink"], padx=8, pady=2)
        self.cam_badge.pack(side=tk.RIGHT)

        tk.Label(left_col, text="Live eye tracking running directly on your laptop.", font=("Segoe UI", 8), fg=THEME["muted"], bg=THEME["card"]).pack(anchor="w", pady=(2, 6))

        self.cam_viewport = tk.Frame(left_col, bg=THEME["camera_bg"])
        self.cam_viewport.pack(fill=tk.BOTH, expand=True, pady=4)

        self.camera_canvas = tk.Label(
            self.cam_viewport,
            bg=THEME["camera_bg"],
            text="Camera is off.\nFrames remain strictly local.",
            font=("Segoe UI", 9),
            fg="#9e9e9e",
        )
        self.camera_canvas.place(relx=0.5, rely=0.5, anchor="center")

        cam_actions = tk.Frame(left_col, bg=THEME["card"])
        cam_actions.pack(fill=tk.X, pady=(8, 2))
        self.cam_start_btn = ttk.Button(cam_actions, text="Start Camera", style="Action.TButton", command=self.start_camera)
        self.cam_start_btn.pack(side=tk.LEFT, padx=(0, 6))
        self.cam_stop_btn = ttk.Button(cam_actions, text="Stop Camera", style="Action.TButton", state="disabled", command=self.stop_camera)
        self.cam_stop_btn.pack(side=tk.LEFT)

        self.cam_meta_label = tk.Label(left_col, text="Face: idle  |  Eyes: idle  |  Blink State: idle", font=("Segoe UI", 8), fg=THEME["muted"], bg=THEME["card"])
        self.cam_meta_label.pack(anchor="w", pady=(4, 0))

        right_col = tk.Frame(container, bg=THEME["card"], relief="solid", bd=1, padx=16, pady=16)
        right_col.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        tk.Label(right_col, text="Controls & Notifications", font=("Segoe UI", 11, "bold"), fg=THEME["ink"], bg=THEME["card"]).pack(anchor="w")

        toggles = tk.Frame(right_col, bg=THEME["card"])
        toggles.pack(fill=tk.X, pady=(8, 4))

        tk.Checkbutton(toggles, text="Windows Push Notifications (Toast Banners)", variable=self.push_enabled, bg=THEME["card"], anchor="w").pack(fill=tk.X, pady=2)
        tk.Checkbutton(toggles, text="Spoken Voice Reminders (Offline TTS)", variable=self.voice_enabled, bg=THEME["card"], anchor="w").pack(fill=tk.X, pady=2)
        tk.Checkbutton(toggles, text="Blink Reminder Alerts (Every 60s)", variable=self.blink_reminders_enabled, bg=THEME["card"], anchor="w").pack(fill=tk.X, pady=2)
        tk.Checkbutton(toggles, text="Posture Prompts (Every 120s)", variable=self.posture_reminders_enabled, bg=THEME["card"], anchor="w").pack(fill=tk.X, pady=2)

        warmth_box = tk.Frame(right_col, bg=THEME["card"], pady=6)
        warmth_box.pack(fill=tk.X)
        tk.Checkbutton(warmth_box, text="Display Gamma Warmth", variable=self.warmth_enabled, bg=THEME["card"], command=self._on_warmth_toggle, anchor="w").pack(fill=tk.X)

        s_box = tk.Frame(warmth_box, bg=THEME["card"])
        s_box.pack(fill=tk.X, pady=(4, 0))
        self.warmth_val_label = tk.Label(s_box, text=f"{self.warmth_level.get()}%", font=("Segoe UI", 8, "bold"), fg=THEME["muted"], bg=THEME["card"])
        self.warmth_val_label.pack(side=tk.RIGHT)
        self.warmth_slider = ttk.Scale(s_box, from_=0, to=100, variable=self.warmth_level, command=self._on_slider_move)
        self.warmth_slider.pack(fill=tk.X, expand=True)

        self.warmth_note = tk.Label(right_col, text="Display gamma modifies color balance on Windows displays.", font=("Segoe UI", 8), fg=THEME["muted"], bg=THEME["card"], wraplength=380, justify="left")
        self.warmth_note.pack(anchor="w", pady=(0, 8))

        summary_actions = tk.Frame(right_col, bg=THEME["card"])
        summary_actions.pack(fill=tk.X, pady=(16, 0))
        ttk.Button(summary_actions, text="Export JSON Summary", style="Action.TButton", command=self.export_json).pack(side=tk.RIGHT)

    def _build_footer(self):
        tk.Label(self, text="Nethra · 100% Offline & Private Eye Comfort Engine", font=("Segoe UI", 8), fg=THEME["muted"], bg=THEME["bg"], pady=8).pack(side=tk.BOTTOM)

    def trigger_notification(self, title: str, message: str, count: bool = True, speak: bool = True):
        if count:
            self.total_reminders += 1

        if self.push_enabled.get():
            send_windows_push_notification(title, message)

        if speak and self.voice_enabled.get():
            self.speaker.speak(f"{title}. {message}")

    def toggle_session(self):
        self.session_running = not self.session_running
        if self.session_running:
            self.session_btn.configure(text="End Session")
            self.status_badge.configure(text="● Session Active", bg=THEME["badge_on"], fg=THEME["badge_on_ink"])
            self.trigger_notification("Nethra Session Started", "Eye comfort and posture tracking is now active.", count=False, speak=False)
        else:
            self.session_btn.configure(text="Start Session")
            self.status_badge.configure(text="● Session Idle", bg=THEME["badge_off"], fg=THEME["badge_off_ink"])
            self.trigger_notification("Nethra Session Ended", f"Total work duration: {self._format_time(self.session_seconds)}", count=False, speak=False)

    def _session_tick(self):
        if self.session_running:
            self.session_seconds += 1
            self.session_label.configure(text=self._format_time(self.session_seconds))
            score = max(60, 100 - (self.session_seconds // 300) * 2)
            self.comfort_score_label.configure(text=str(score))

            if self.session_seconds % 60 == 0 and self.blink_reminders_enabled.get():
                self.trigger_notification("Blink Reminder", "Take a moment to blink gently and look into the distance.")

            if self.session_seconds % 120 == 0 and self.posture_reminders_enabled.get():
                self.trigger_notification("Posture Check", "Relax your shoulders, align your neck, and sit upright.")

        self.after(1000, self._session_tick)

    def _format_time(self, total_seconds: int) -> str:
        m = total_seconds // 60
        s = total_seconds % 60
        return f"{m:02d}:{s:02d}"

    def _on_slider_move(self, value):
        val = int(float(value))
        self.warmth_val_label.configure(text=f"{val}%")
        if self.warmth_enabled.get():
            apply_windows_warmth(True, val)

    def _on_warmth_toggle(self):
        active = self.warmth_enabled.get()
        level = self.warmth_level.get()
        ok, msg = apply_windows_warmth(active, level)
        self.warmth_note.configure(text=msg)
        if not ok and active:
            self.warmth_enabled.set(False)

    def start_camera(self):
        if cv2 is None or Image is None or ImageTk is None:
            messagebox.showerror(
                "Missing Required Libraries",
                "Install the required libraries in the VS Code terminal:\n"
                "python -m pip install opencv-python pillow",
            )
            return

        if not hasattr(cv2, "VideoCapture") or not (
            hasattr(cv2, "CascadeClassifier")
            or hasattr(getattr(cv2, "cv2", None), "CascadeClassifier")
        ):
            messagebox.showerror(
                "OpenCV Installation Problem",
                "Your cv2 installation is incomplete or conflicting.\n\n"
                "Run these commands in the VS Code terminal:\n"
                "python -m pip uninstall opencv-python opencv-contrib-python opencv-python-headless -y\n"
                "python -m pip install opencv-python pillow\n\n"
                "Then restart VS Code and run app.py again.",
            )
            return

        # Use the Haar cascade files included with OpenCV; no manual XML download needed.
        cascade_data = getattr(cv2, "data", None)
        cascade_dir = Path(getattr(cascade_data, "haarcascades", ""))
        face_xml = cascade_dir / "haarcascade_frontalface_default.xml"
        eye_xml = cascade_dir / "haarcascade_eye.xml"
        if not face_xml.exists() or not eye_xml.exists():
            messagebox.showerror(
                "OpenCV Detector Files Missing",
                "The Haar cascade files are missing. Reinstall OpenCV with:\n"
                "python -m pip install --force-reinstall opencv-python",
            )
            return

        if self.camera_running:
            return

        self.latest_frame = None
        self.camera_running = True
        self.cam_start_btn.configure(state="disabled")
        self.cam_stop_btn.configure(state="normal")
        self.cam_badge.configure(text="Connecting", bg=THEME["badge_on"], fg=THEME["badge_on_ink"])
        self.camera_canvas.configure(image="", text="Connecting to webcam hardware...")

        self.camera_thread = threading.Thread(
            target=self._camera_worker,
            args=(face_xml, eye_xml),
            daemon=True,
        )
        self.camera_thread.start()

    def stop_camera(self):
        self.camera_running = False
        self.cam_start_btn.configure(state="normal")
        self.cam_stop_btn.configure(state="disabled")
        self.cam_badge.configure(text="Off", bg=THEME["badge_off"], fg=THEME["badge_off_ink"])
        self.cam_meta_label.configure(text="Face: idle  |  Eyes: idle  |  Blink State: idle")
        self.blink_hint_label.configure(text="Camera stopped")
        self.camera_canvas.configure(image="", text="Camera preview stopped.")

    def _camera_error(self, message):
        """Update Tkinter widgets from the main UI thread after camera startup fails."""
        self.camera_running = False
        self.cam_start_btn.configure(state="normal")
        self.cam_stop_btn.configure(state="disabled")
        self.cam_badge.configure(text="Error", bg="#f8e8e6", fg="#9b3028")
        self.camera_canvas.configure(image="", text="Camera unavailable. Check the error message.")
        self.blink_hint_label.configure(text="Camera unavailable")
        messagebox.showerror("Nethra Camera", message)

    def _camera_worker(self, face_xml_path, eye_xml_path):
        cap = None
        try:
            backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF] if sys.platform == "win32" else [cv2.CAP_ANY]
            for idx in (0, 1, 2):
                for backend in backends:
                    temp_cap = cv2.VideoCapture(idx, backend)
                    if temp_cap is not None and temp_cap.isOpened():
                        cap = temp_cap
                        break
                    if temp_cap is not None:
                        temp_cap.release()
                if cap is not None:
                    break

            if cap is None:
                self.after(0, self._camera_error,
                           "No webcam could be opened. Close other apps using the camera, "
                           "check Windows Camera permissions, and try again.")
                return

            cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

            face_cascade = get_cascade_classifier(face_xml_path)
            eye_cascade = get_cascade_classifier(eye_xml_path)

            frame_count = 0
            cached_face = None

            while self.camera_running:
                ok, frame = cap.read()
                if not ok or frame is None:
                    time.sleep(0.05)
                    continue

                frame_count += 1
                frame = cv2.flip(frame, 1)
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                small_gray = cv2.resize(gray, (0, 0), fx=0.5, fy=0.5)

                if frame_count % 3 == 0 or cached_face is None:
                    faces = face_cascade.detectMultiScale(
                        small_gray, scaleFactor=1.2, minNeighbors=4, minSize=(45, 45)
                    )
                    if len(faces) > 0:
                        fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
                        cached_face = (fx * 2, fy * 2, fw * 2, fh * 2)
                    else:
                        cached_face = None

                face_found = cached_face is not None
                eyes_found_count = 0

                if face_found:
                    x, y, w, h = cached_face
                    cv2.rectangle(frame, (x, y), (x + w, y + h), (50, 160, 120), 2)
                    y_end = min(gray.shape[0], y + int(h * 0.58))
                    x_end = min(gray.shape[1], x + w)
                    roi_gray = gray[max(0, y):y_end, max(0, x):x_end]
                    roi_color = frame[max(0, y):y_end, max(0, x):x_end]

                    if roi_gray.size > 0:
                        eyes = eye_cascade.detectMultiScale(
                            roi_gray, scaleFactor=1.1, minNeighbors=4, minSize=(16, 16)
                        )
                        eyes_found_count = len(eyes)
                        for ex, ey, ew, eh in eyes[:2]:
                            cv2.rectangle(roi_color, (ex, ey), (ex + ew, ey + eh), (80, 200, 150), 2)

                        eyes_currently_open = eyes_found_count >= 1
                        if not eyes_currently_open:
                            self.no_eye_frame_count += 1
                            if self.last_eye_present:
                                self.last_eye_present = False
                        else:
                            if not self.last_eye_present:
                                if 1 <= self.no_eye_frame_count <= 6:
                                    self.blinks += 1
                                self.last_eye_present = True
                                self.no_eye_frame_count = 0
                else:
                    self.last_eye_present = True
                    self.no_eye_frame_count = 0

                render_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                with self.frame_lock:
                    self.latest_frame = render_rgb
                    self.face_detected = face_found
                    self.eyes_detected_count = eyes_found_count

                time.sleep(0.01)

        except Exception as exc:
            self.after(0, self._camera_error, f"{exc}")
        finally:
            if cap is not None:
                cap.release()

    def _render_camera_feed(self):
        if self.camera_running:
            with self.frame_lock:
                frame = self.latest_frame
                face_ok = self.face_detected
                eye_count = self.eyes_detected_count

            if frame is not None:
                orig_h, orig_w, _ = frame.shape
                vp_w = max(self.cam_viewport.winfo_width(), 100)
                vp_h = max(self.cam_viewport.winfo_height(), 100)

                aspect = orig_w / orig_h
                target_w = vp_w
                target_h = int(target_w / aspect)

                if target_h > vp_h:
                    target_h = vp_h
                    target_w = int(target_h * aspect)

                img = Image.fromarray(frame)
                resampling = getattr(Image, "Resampling", Image)
                bilinear = getattr(resampling, "BILINEAR")
                img = img.resize((target_w, target_h), bilinear)
                photo_image_factory = getattr(ImageTk, "PhotoImage")
                imgtk = photo_image_factory(image=img)

                self.camera_canvas.configure(image=imgtk, text="")
                setattr(self.camera_canvas, "image", imgtk)

                face_str = "Detected" if face_ok else "No face"
                eye_str = f"{eye_count} Open" if eye_count > 0 else "Closed / Blinking"
                self.cam_meta_label.configure(
                    text=f"Face: {face_str}  |  Eyes: {eye_str}  |  Blinks Recorded: {self.blinks}"
                )
                self.blink_metric_label.configure(text=str(self.blinks))
                self.blink_hint_label.configure(text="Optical blink verification active")

        self.after(25, self._render_camera_feed)

    def export_json(self):
        summary = {
            "application": "Nethra",
            "exported_at": datetime.now().isoformat(),
            "session_duration": self._format_time(self.session_seconds),
            "session_seconds": self.session_seconds,
            "reminders_issued": self.total_reminders,
            "verified_blinks": self.blinks,
            "warmth_enabled": self.warmth_enabled.get(),
            "warmth_level": self.warmth_level.get(),
            "voice_alerts": self.voice_enabled.get(),
        }

        file_path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON Files", "*.json")],
            initialfile="nethra-summary.json",
            title="Save Session Summary",
        )
        if file_path:
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(summary, f, indent=2)
                messagebox.showinfo("Export Successful", f"Saved summary to:\n{file_path}")
            except Exception as e:
                messagebox.showerror("Export Failed", f"Could not save file: {e}")

    def _on_close(self):
        self.camera_running = False
        if self.warmth_enabled.get():
            apply_windows_warmth(False)
        self.destroy()


def main():
    app = NethraDesktopApp()
    app.mainloop()


if __name__ == "__main__":
    main()