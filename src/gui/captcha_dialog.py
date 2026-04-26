"""Manual CAPTCHA fallback dialog — shown when OCR fails."""
from __future__ import annotations

import io
from typing import Optional

import customtkinter as ctk
from PIL import Image


class ManualCaptchaDialog(ctk.CTkToplevel):
    def __init__(self, parent, image_bytes: bytes, attempt: int, client_name: str) -> None:
        super().__init__(parent)
        self.title(f"CAPTCHA — {client_name}")
        self.geometry("420x320")
        self.resizable(False, False)
        self.grab_set()
        self.transient(parent)

        self.value: Optional[str] = None

        ctk.CTkLabel(
            self,
            text=f"Auto-solve failed (attempt {attempt}). Please type the CAPTCHA below:",
            wraplength=380,
        ).pack(pady=(14, 6))

        # Show the captcha image (scaled up 3x for readability)
        try:
            pil_img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            w, h = pil_img.size
            scale = 3
            ctk_img = ctk.CTkImage(
                light_image=pil_img,
                dark_image=pil_img,
                size=(w * scale, h * scale),
            )
            ctk.CTkLabel(self, text="", image=ctk_img).pack(pady=8)
            self._img_ref = ctk_img
        except Exception:  # noqa: BLE001
            ctk.CTkLabel(self, text="(could not display CAPTCHA image)").pack(pady=8)

        self._entry = ctk.CTkEntry(self, placeholder_text="6-character CAPTCHA", height=36, width=240)
        self._entry.pack(pady=8)
        self._entry.bind("<Return>", lambda _e: self._submit())

        row = ctk.CTkFrame(self, fg_color="transparent")
        row.pack(pady=12)
        ctk.CTkButton(row, text="Skip Client", width=120, fg_color="gray",
                      command=self._cancel).pack(side="left", padx=6)
        ctk.CTkButton(row, text="Submit", width=120,
                      command=self._submit).pack(side="left", padx=6)

        self.protocol("WM_DELETE_WINDOW", self._cancel)

        # Force focus into the CAPTCHA input box once the window is fully
        # rendered, so the user can start typing immediately. A plain
        # focus_set() before the window is mapped is silently ignored on
        # Windows, so we delay slightly and use focus_force().
        self.after(120, self._focus_input)

    def _focus_input(self) -> None:
        try:
            self.lift()
            self.focus_force()
            self._entry.focus_force()
            # Place the text cursor at the end of the (empty) field
            self._entry.icursor("end")
        except Exception:
            pass

    def _submit(self) -> None:
        v = (self._entry.get() or "").strip()
        if not v:
            return
        self.value = v
        self.destroy()

    def _cancel(self) -> None:
        self.value = None
        self.destroy()


def prompt_manual_captcha(parent, image_bytes: bytes, attempt: int,
                           client_name: str) -> Optional[str]:
    dlg = ManualCaptchaDialog(parent, image_bytes, attempt, client_name)
    parent.wait_window(dlg)
    return dlg.value
