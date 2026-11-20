import asyncio
import struct
import threading
import tkinter as tk
from tkinter import ttk
from bleak import BleakClient, BleakScanner

WRITE_CHAR_UUID = "3d115840-6e0b-11e4-b24f-0002a5d5c51b"
NOTIFY_CHAR_UUID = "f833d6c0-6e0b-11e4-9136-0002a5d5c51b"


def _crc16_table() -> list[int]:
    table = []
    for i in range(256):
        crc = i << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x8005) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
        table.append(crc)
    return table


CRC16_TABLE = _crc16_table()


def build_packet(cmd: bytes, body: bytes = b"") -> bytes:
    length = 2 + len(body) + 3
    pre = bytes([0x7B, 0x41, 0x00, length]) + cmd + body
    crc = 0
    for b in pre:
        crc = ((crc << 8) ^ CRC16_TABLE[((crc >> 8) ^ b) & 0xFF]) & 0xFFFF
    return pre + crc.to_bytes(2, "big") + b"\x7D"


class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Honeywell HTRAM Control")
        self.root.geometry("350x250")
        self.root.resizable(False, False)

        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()

        self.client: BleakClient | None = None
        self.heartbeat_task: asyncio.Task | None = None

        self.status_var = tk.StringVar(value="Disconnected")
        self.sound_var = tk.StringVar(value="Sound: —")
        self.screen_var = tk.StringVar(value="Screen: —")

        ttk.Label(root, textvariable=self.status_var, font=("Segoe UI", 10, "bold")).pack(pady=(12, 6))

        self.btn_connect = ttk.Button(root, text="Connect to HTRAM", command=self.toggle_connect)
        self.btn_connect.pack(fill="x", padx=20, pady=4)

        ttk.Separator(root).pack(fill="x", padx=15, pady=10)

        ttk.Label(root, textvariable=self.sound_var).pack()
        f_sound = ttk.Frame(root)
        f_sound.pack(fill="x", padx=20, pady=4)
        self.btn_sound_on = ttk.Button(f_sound, text="Sound ON", state="disabled", command=lambda: self.set_sound(True))
        self.btn_sound_off = ttk.Button(f_sound, text="Sound OFF", state="disabled", command=lambda: self.set_sound(False))
        self.btn_sound_on.pack(side="left", expand=True, fill="x", padx=(0, 4))
        self.btn_sound_off.pack(side="left", expand=True, fill="x", padx=(4, 0))

        ttk.Label(root, textvariable=self.screen_var).pack(pady=(8, 0))
        f_screen = ttk.Frame(root)
        f_screen.pack(fill="x", padx=20, pady=4)
        self.btn_screen_on = ttk.Button(f_screen, text="Screen ON", state="disabled", command=lambda: self.set_screen_off(False))
        self.btn_screen_off = ttk.Button(f_screen, text="Screen OFF", state="disabled", command=lambda: self.set_screen_off(True))
        self.btn_screen_on.pack(side="left", expand=True, fill="x", padx=(0, 4))
        self.btn_screen_off.pack(side="left", expand=True, fill="x", padx=(4, 0))

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def set_controls_state(self, enabled: bool):
        state = "normal" if enabled else "disabled"
        for btn in (self.btn_sound_on, self.btn_sound_off, self.btn_screen_on, self.btn_screen_off):
            btn.config(state=state)

    def on_notify(self, _, data: bytearray):
        if len(data) >= 10 and data[4:6] == b"\x27\x23":
            state = "ON" if data[9] != 0 else "OFF"
            self.root.after(0, lambda: self.sound_var.set(f"Sound: {state}"))
        elif len(data) >= 13 and data[4:6] == b"\x41\x43":
            _, _, screen_off = struct.unpack(">HHH", data[7:13])
            state = "ON" if screen_off == 0 else "OFF"
            self.root.after(0, lambda: self.screen_var.set(f"Screen: {state}"))

    def on_ble_disconnect(self, _):
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
        self.client = None
        self.root.after(0, self._on_disconnected_ui)

    def toggle_connect(self):
        if self.client and self.client.is_connected:
            asyncio.run_coroutine_threadsafe(self._disconnect(), self.loop)
        else:
            self.btn_connect.config(state="disabled")
            self.status_var.set("Scanning for HTRAM...")
            asyncio.run_coroutine_threadsafe(self._connect(), self.loop)

    async def _connect(self):
        device = await BleakScanner.find_device_by_filter(
            lambda d, _: bool(d.name and d.name.startswith("HTRAM")),
            timeout=10.0,
        )
        if not device:
            self.root.after(0, lambda: self._on_error_ui("Not found (wake device with button)"))
            return

        for attempt in range(1, 4):
            client = None
            try:
                self.root.after(0, lambda a=attempt: self.status_var.set(f"Connecting ({a}/3)..."))
                client = BleakClient(device, timeout=15.0, disconnected_callback=self.on_ble_disconnect)
                await client.connect()
                await asyncio.sleep(0.6)
                if not client.is_connected:
                    raise RuntimeError("Dropped after connect")

                await client.start_notify(NOTIFY_CHAR_UUID, self.on_notify)
                await client.write_gatt_char(WRITE_CHAR_UUID, build_packet(b"\x74\x58", b"\x01\x01\x00"))
                await asyncio.sleep(0.4)
                await client.write_gatt_char(WRITE_CHAR_UUID, build_packet(b"\x26\x23", b"\x01\x00"))
                await asyncio.sleep(0.3)
                await client.write_gatt_char(WRITE_CHAR_UUID, build_packet(b"\x40\x43", b"\x04\x00\x60\x06"))

                self.client = client
                self.heartbeat_task = asyncio.create_task(self._heartbeat())
                self.root.after(0, lambda n=device.name: self._on_connected_ui(n))
                return
            except Exception:
                if client:
                    try:
                        await client.disconnect()
                    except Exception:
                        pass
                await asyncio.sleep(1.0)

        self.root.after(0, lambda: self._on_error_ui("Connection failed (pair in OS & wake)"))

    async def _heartbeat(self):
        try:
            while self.client and self.client.is_connected:
                await asyncio.sleep(10)
                if self.client and self.client.is_connected:
                    await self.client.write_gatt_char(WRITE_CHAR_UUID, build_packet(b"\x24\x01", b"\x01"))
        except Exception:
            pass

    async def _disconnect(self):
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
            self.client = None
        self.root.after(0, self._on_disconnected_ui)

    def _on_connected_ui(self, name: str):
        self.status_var.set(f"Connected: {name}")
        self.btn_connect.config(text="Disconnect", state="normal")
        self.set_controls_state(True)

    def _on_disconnected_ui(self):
        self.status_var.set("Disconnected")
        self.sound_var.set("Sound: —")
        self.screen_var.set("Screen: —")
        self.btn_connect.config(text="Connect to HTRAM", state="normal")
        self.set_controls_state(False)

    def _on_error_ui(self, msg: str):
        self.status_var.set(msg)
        self.btn_connect.config(text="Connect to HTRAM", state="normal")
        self.set_controls_state(False)

    def set_sound(self, enabled: bool):
        asyncio.run_coroutine_threadsafe(self._send_sound(enabled), self.loop)

    async def _send_sound(self, enabled: bool):
        if not self.client or not self.client.is_connected:
            return
        await self.client.write_gatt_char(
            WRITE_CHAR_UUID,
            build_packet(b"\x26\x43", b"\x01\x00\x00" + bytes([1 if enabled else 0])),
        )
        await asyncio.sleep(0.3)
        await self.client.write_gatt_char(WRITE_CHAR_UUID, build_packet(b"\x26\x23", b"\x01\x00"))

    def set_screen_off(self, off: bool):
        asyncio.run_coroutine_threadsafe(self._send_screen(off), self.loop)

    async def _send_screen(self, off: bool):
        if not self.client or not self.client.is_connected:
            return
        await self.client.write_gatt_char(
            WRITE_CHAR_UUID,
            build_packet(b"\x42\x43", b"\x04\x00\x20\x00" + struct.pack(">H", 1 if off else 0)),
        )
        await asyncio.sleep(0.3)
        await self.client.write_gatt_char(WRITE_CHAR_UUID, build_packet(b"\x40\x43", b"\x04\x00\x60\x06"))

    def on_close(self):
        if self.client and self.client.is_connected:
            asyncio.run_coroutine_threadsafe(self._disconnect(), self.loop)
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    App(root)
    root.mainloop()
