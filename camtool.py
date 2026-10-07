"""Pan/tilt/zoom sliders and one-click presets for UVC cameras (DirectShow IAMCameraControl)."""
import json
import sys
import tkinter as tk
from ctypes import POINTER, byref, c_long, c_ulong
from pathlib import Path
from tkinter import simpledialog, ttk

import comtypes
import comtypes.client
from comtypes import GUID, COMMETHOD, HRESULT, IUnknown, STDMETHOD
from comtypes.automation import VARIANT
from comtypes.persist import IPropertyBag

CAMERA_NAME_FILTER = "Yealink"  # only cameras whose name contains this
PRESETS_FILE = Path(sys.executable if getattr(sys, "frozen", False) else __file__).with_name("presets.json")
PROPS = {"Pan": 0, "Tilt": 1, "Zoom": 3}  # CameraControl_* enum
FLAG_MANUAL = 2
STEP = {0: 1, 1: 1, 3: 5}  # keyboard step per press for pan, tilt, zoom; flip signs in App bindings if a direction is inverted
IDLE_BG, HOVER_BG = "#e8e8e8", "#b8d8ff"


class IMoniker(IUnknown):
    _iid_ = GUID("{0000000F-0000-0000-C000-000000000046}")
    _methods_ = [
        # IPersist / IPersistStream slots we never call, declared only for vtable order
        STDMETHOD(HRESULT, "GetClassID", []),
        STDMETHOD(HRESULT, "IsDirty", []),
        STDMETHOD(HRESULT, "Load", []),
        STDMETHOD(HRESULT, "Save", []),
        STDMETHOD(HRESULT, "GetSizeMax", []),
        COMMETHOD([], HRESULT, "BindToObject",
                  (["in"], POINTER(IUnknown), "pbc"), (["in"], POINTER(IUnknown), "pmkToLeft"),
                  (["in"], POINTER(GUID), "riid"), (["out"], POINTER(POINTER(IUnknown)), "ppv")),
        COMMETHOD([], HRESULT, "BindToStorage",
                  (["in"], POINTER(IUnknown), "pbc"), (["in"], POINTER(IUnknown), "pmkToLeft"),
                  (["in"], POINTER(GUID), "riid"), (["out"], POINTER(POINTER(IUnknown)), "ppv")),
    ]


class IEnumMoniker(IUnknown):
    _iid_ = GUID("{00000102-0000-0000-C000-000000000046}")
    _methods_ = [
        COMMETHOD([], HRESULT, "Next", (["in"], c_ulong, "celt"),
                  (["out"], POINTER(POINTER(IMoniker)), "rgelt"), (["out"], POINTER(c_ulong), "fetched")),
    ]


class ICreateDevEnum(IUnknown):
    _iid_ = GUID("{29840822-5B84-11D0-BD3B-00A0C911CE86}")
    _methods_ = [
        COMMETHOD([], HRESULT, "CreateClassEnumerator", (["in"], POINTER(GUID), "clsid"),
                  (["out"], POINTER(POINTER(IEnumMoniker)), "ppEnum"), (["in"], c_ulong, "flags")),
    ]


class IAMCameraControl(IUnknown):
    _iid_ = GUID("{C6E13370-30AC-11d0-A18C-00A0C9118956}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetRange", (["in"], c_long, "prop"),
                  *[(["out"], POINTER(c_long), n) for n in ("min", "max", "step", "default", "caps")]),
        COMMETHOD([], HRESULT, "Set", (["in"], c_long, "prop"), (["in"], c_long, "value"), (["in"], c_long, "flags")),
        COMMETHOD([], HRESULT, "Get", (["in"], c_long, "prop"),
                  (["out"], POINTER(c_long), "value"), (["out"], POINTER(c_long), "flags")),
    ]


CLSID_SystemDeviceEnum = GUID("{62BE5D10-60EB-11d0-BD3B-00A0C911CE86}")
CLSID_VideoInputDeviceCategory = GUID("{860BB310-5D01-11d0-BD3B-00A0C911CE86}")


def find_cameras():
    """Return [(name, device_path, IAMCameraControl)] for matching cameras."""
    dev_enum = comtypes.client.CreateObject(CLSID_SystemDeviceEnum, interface=ICreateDevEnum)
    enum = dev_enum.CreateClassEnumerator(byref(CLSID_VideoInputDeviceCategory), 0)
    cams = []
    while enum:  # NULL when no video devices
        moniker, fetched = enum.Next(1)
        if not fetched:
            break
        bag = moniker.BindToStorage(None, None, byref(IPropertyBag._iid_)).QueryInterface(IPropertyBag)
        name = bag.Read("FriendlyName", VARIANT(), None)
        if CAMERA_NAME_FILTER.lower() not in name.lower():
            continue
        path = bag.Read("DevicePath", VARIANT(), None)
        ctl = moniker.BindToObject(None, None, byref(IUnknown._iid_)).QueryInterface(IAMCameraControl)
        cams.append((name, path, ctl))
    return cams


def load_presets():
    try:
        return json.loads(PRESETS_FILE.read_text())
    except FileNotFoundError:
        return {}


class App(tk.Tk):
    def __init__(self, cams):
        super().__init__()
        self.title("Yealink PTZ")
        self.cams = cams  # [(name, path, ctl)]
        self.vars = []    # per camera: {prop: IntVar}
        self.ranges = []  # per camera: {prop: (min, max)}
        self.presets = load_presets()
        self.hovered = None  # camera index under the mouse in the keyboard tab

        self.tabs = ttk.Notebook(self)
        self.tabs.pack(fill="both", expand=True, padx=6, pady=6)
        sliders = tk.Frame(self.tabs)
        keys = tk.Frame(self.tabs)
        self.tabs.add(sliders, text="Sliders")
        self.tabs.add(keys, text="Keyboard")

        for i, (name, path, ctl) in enumerate(cams):
            letter = chr(ord("A") + i)
            frame = tk.LabelFrame(sliders, text=f"{letter}: {name}", padx=6, pady=6)
            frame.grid(row=0, column=i, sticky="n", padx=6, pady=6)
            cam_vars, cam_ranges = {}, {}
            for r, (label, prop) in enumerate(PROPS.items()):
                lo, hi, step, _default, _caps = ctl.GetRange(prop)
                var = tk.IntVar(value=ctl.Get(prop)[0])
                tk.Label(frame, text=label).grid(row=r, column=0, sticky="w")
                tk.Scale(frame, from_=lo, to=hi, resolution=max(step, 1), orient="horizontal", length=260,
                         variable=var, command=lambda v, c=ctl, p=prop: c.Set(p, int(float(v)), FLAG_MANUAL)
                         ).grid(row=r, column=1)
                cam_vars[prop], cam_ranges[prop] = var, (lo, hi)
            self.vars.append(cam_vars)
            self.ranges.append(cam_ranges)

            pane = tk.Label(keys, text=letter, font=("Segoe UI", 64, "bold"), width=4, height=2,
                            bg=IDLE_BG, relief="ridge", bd=2)
            pane.grid(row=0, column=i, padx=6, pady=6, sticky="nsew")
            pane.bind("<Enter>", lambda e, i=i, w=pane: (setattr(self, "hovered", i), w.config(bg=HOVER_BG), w.focus_set()))
            pane.bind("<Leave>", lambda e, w=pane: (setattr(self, "hovered", None), w.config(bg=IDLE_BG)))
        tk.Label(keys, text="Hover a camera, then: arrows = pan/tilt, Ctrl+Up/Down = zoom", fg="gray"
                 ).grid(row=1, column=0, columnspan=max(len(cams), 1))

        for key, prop, sign in (("Left", PROPS["Pan"], -1), ("Right", PROPS["Pan"], 1),
                                ("Up", PROPS["Tilt"], -1), ("Down", PROPS["Tilt"], 1),
                                ("Control-Up", PROPS["Zoom"], 1), ("Control-Down", PROPS["Zoom"], -1)):
            self.bind(f"<{key}>", lambda e, p=prop, s=sign: self.nudge(p, s))

        bar = tk.Frame(self)
        bar.pack(fill="x", padx=6)
        tk.Button(bar, text="Save preset…", command=self.save_preset).pack(side="left")
        tk.Label(bar, text="(right-click a preset to delete)", fg="gray").pack(side="left", padx=6)
        self.preset_bar = tk.Frame(self)
        self.preset_bar.pack(fill="x", padx=6, pady=6)
        self.refresh_presets()

    def nudge(self, prop, sign):
        if self.hovered is None or self.tabs.index("current") != 1:
            return
        i = self.hovered
        ctl = self.cams[i][2]
        lo, hi = self.ranges[i][prop]
        # read the camera, not the slider: the camera can move pan/tilt itself (e.g. while zooming)
        value = min(hi, max(lo, ctl.Get(prop)[0] + sign * STEP[prop]))
        ctl.Set(prop, value, FLAG_MANUAL)
        self.vars[i][prop].set(value)

    def save_preset(self):
        name = simpledialog.askstring("Save preset", "Preset name:", parent=self)
        if not name:
            return
        self.presets[name] = {path: {str(p): ctl.Get(p)[0] for p in PROPS.values()} for _, path, ctl in self.cams}
        self.write_presets()

    def apply_preset(self, name):
        for i, (_, path, ctl) in enumerate(self.cams):
            target = {int(p): v for p, v in self.presets[name].get(path, {}).items()}  # cameras not in preset are left alone
            zoom = PROPS["Zoom"]
            if zoom in target:
                self.set_prop(i, zoom, target.pop(zoom))
            # zoom moves gradually; pan/tilt sent mid-zoom get overridden, so wait for it to arrive
            self.after_zoom(i, ctl, lambda i=i, t=target: [self.set_prop(i, p, v) for p, v in t.items()])

    def after_zoom(self, i, ctl, then, tries=40):
        if tries and ctl.Get(PROPS["Zoom"])[0] != self.vars[i][PROPS["Zoom"]].get():
            self.after(100, self.after_zoom, i, ctl, then, tries - 1)  # ponytail: 4s cap, raise if big zooms take longer
        else:
            then()

    def set_prop(self, i, prop, value):
        self.cams[i][2].Set(prop, value, FLAG_MANUAL)
        self.vars[i][prop].set(value)

    def delete_preset(self, name):
        del self.presets[name]
        self.write_presets()

    def write_presets(self):
        PRESETS_FILE.write_text(json.dumps(self.presets, indent=2))
        self.refresh_presets()

    def refresh_presets(self):
        for w in self.preset_bar.winfo_children():
            w.destroy()
        for name in self.presets:
            b = tk.Button(self.preset_bar, text=name, width=12, command=lambda n=name: self.apply_preset(n))
            b.bind("<Button-3>", lambda e, n=name: self.delete_preset(n))
            b.pack(side="left", padx=2)


if __name__ == "__main__":
    cams = find_cameras()
    if not cams:
        raise SystemExit(f"No cameras matching '{CAMERA_NAME_FILTER}' found.")
    App(cams).mainloop()
