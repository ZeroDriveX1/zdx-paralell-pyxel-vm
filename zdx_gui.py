"""
ZDX Parallel Pyxel VM desktop GUI.

Cross-platform interface using Python tkinter.
Supported targets:
- Windows
- Linux
- macOS

Provides a stable desktop shell around node operations.
"""

from tkinter import Tk, Label, Button, Text

from zdx_sync import FrameSync


class ZDXGui:
    def __init__(self):
        self.root = Tk()
        self.root.title("ZDX Parallel Pyxel VM")
        self.log = Text(self.root, height=12, width=60)

        Label(
            self.root,
            text="ZeroDriveX Parallel Pyxel VM Node",
        ).pack()

        Button(
            self.root,
            text="Check GUI Status",
            command=self.status,
        ).pack()

        self.log.pack()

    def status(self):
        self.log.insert("end", "ZDX GUI online\n")

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    ZDXGui().run()
