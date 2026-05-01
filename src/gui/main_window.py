"""Main application window."""
from __future__ import annotations

import logging
import queue
import threading
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Optional

import customtkinter as ctk

from .. import config, logger as app_logger
from ..crypto_utils import Vault
from ..excel_io import (
    Client, ClientResult, create_sample_excel, read_clients,
)
from ..orchestrator import BatchOptions, run_batch
from .captcha_dialog import prompt_manual_captcha


log = logging.getLogger("gstr2b.gui.main")


class MainWindow(ctk.CTk):

    def __init__(self, vault: Vault) -> None:
        super().__init__()
        self.vault = vault
        self.title(f"{config.APP_NAME} v{config.APP_VERSION}")
        self.geometry("1100x720")
        self.minsize(900, 600)

        self._clients: list[Client] = []
        self._row_to_client: dict[str, Client] = {}
        self._results_by_row: dict[int, ClientResult] = {}
        self._cancel_event = threading.Event()
        self._worker: Optional[threading.Thread] = None
        self._captcha_response: Optional[str] = None
        self._captcha_event = threading.Event()
        self._captcha_request: Optional[tuple[bytes, int, str]] = None

        self._gui_log_queue = app_logger.get_gui_queue()

        self._build_layout()
        self._poll_log_queue()
        self._poll_captcha_request()

        # Generate a starter sample on first run if none exists (and the
        # bundled template hasn't been copied next to the .exe yet).
        sample_alt = config.ROOT_DIR / "sample_clients_TEMPLATE.xlsx"
        if not config.SAMPLE_EXCEL.exists() and not sample_alt.exists():
            create_sample_excel(config.SAMPLE_EXCEL)
            log.info("Sample clients file created: %s", config.SAMPLE_EXCEL)

    # ------------------------------------------------------------------ UI --

    def _build_layout(self) -> None:
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        top = ctk.CTkFrame(self, corner_radius=0)
        top.grid(row=0, column=0, sticky="ew", padx=0, pady=0)
        for c in range(8):
            top.grid_columnconfigure(c, weight=0)
        top.grid_columnconfigure(7, weight=1)

        ctk.CTkLabel(top, text=config.APP_NAME,
                     font=ctk.CTkFont(size=18, weight="bold")
                     ).grid(row=0, column=0, padx=14, pady=12, sticky="w")

        ctk.CTkButton(top, text="Load Clients Excel...", width=170,
                      command=self._on_load_excel
                      ).grid(row=0, column=1, padx=6, pady=12)

        ctk.CTkLabel(top, text="Year:").grid(row=0, column=2, padx=(16, 4))
        current_year = datetime.now().year
        years = [str(y) for y in range(current_year - 4, current_year + 2)]
        self._year_var = ctk.StringVar(value=str(current_year))
        self._year_dd = ctk.CTkOptionMenu(top, values=years, variable=self._year_var, width=90)
        self._year_dd.grid(row=0, column=3, padx=4)

        ctk.CTkLabel(top, text="Month:").grid(row=0, column=4, padx=(12, 4))
        self._month_var = ctk.StringVar(value=_default_month_name())
        self._month_dd = ctk.CTkOptionMenu(
            top,
            values=["January", "February", "March", "April", "May", "June",
                    "July", "August", "September", "October", "November", "December"],
            variable=self._month_var, width=130,
        )
        self._month_dd.grid(row=0, column=5, padx=4)

        self._headless_var = ctk.BooleanVar(value=True)
        ctk.CTkSwitch(top, text="Run hidden", variable=self._headless_var,
                      ).grid(row=0, column=6, padx=14)

        # Right side action buttons
        right = ctk.CTkFrame(top, fg_color="transparent")
        right.grid(row=0, column=7, sticky="e", padx=10)
        self._start_btn = ctk.CTkButton(
            right, text="Start Download", width=160, height=36,
            font=ctk.CTkFont(size=14, weight="bold"),
            command=self._on_start)
        self._start_btn.pack(side="left", padx=4)
        self._stop_btn = ctk.CTkButton(
            right, text="Stop", width=90, height=36, fg_color="#C0392B",
            hover_color="#A93226", command=self._on_stop, state="disabled")
        self._stop_btn.pack(side="left", padx=4)

        # ---------------- middle: client table -------------------------------
        mid = ctk.CTkFrame(self)
        mid.grid(row=1, column=0, sticky="nsew", padx=10, pady=(8, 0))
        mid.grid_rowconfigure(1, weight=1)
        mid.grid_columnconfigure(0, weight=1)

        sel_row = ctk.CTkFrame(mid, fg_color="transparent")
        sel_row.grid(row=0, column=0, sticky="ew", padx=4, pady=4)
        ctk.CTkButton(sel_row, text="Select All", width=110,
                      command=lambda: self._select_all(True)).pack(side="left", padx=4)
        ctk.CTkButton(sel_row, text="Clear", width=80, fg_color="gray",
                      command=lambda: self._select_all(False)).pack(side="left", padx=4)
        self._summary_label = ctk.CTkLabel(sel_row, text="No clients loaded.")
        self._summary_label.pack(side="left", padx=14)

        # ttk.Treeview gives us a real spreadsheet-like table inside customtkinter
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Treeview",
                        background="#FFFFFF", foreground="#1B1B1B",
                        rowheight=26, fieldbackground="#FFFFFF",
                        font=("Segoe UI", 10))
        style.configure("Treeview.Heading",
                        background="#1F4E78", foreground="#FFFFFF",
                        font=("Segoe UI", 10, "bold"))

        table_frame = ctk.CTkFrame(mid, fg_color="transparent")
        table_frame.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)
        table_frame.grid_rowconfigure(0, weight=1)
        table_frame.grid_columnconfigure(0, weight=1)

        cols = ("sel", "sr", "name", "user_id", "gstin", "status", "msg")
        self._tree = ttk.Treeview(
            table_frame, columns=cols, show="headings", selectmode="none",
        )
        widths = {"sel": 50, "sr": 50, "name": 280, "user_id": 160,
                  "gstin": 160, "status": 130, "msg": 260}
        headers = {"sel": "✓", "sr": "Sr", "name": "Client Name",
                   "user_id": "User ID", "gstin": "GSTIN",
                   "status": "Status", "msg": "Message"}
        for c in cols:
            self._tree.heading(c, text=headers[c])
            self._tree.column(c, width=widths[c],
                              anchor="center" if c in ("sel", "sr", "status") else "w")

        self._tree.tag_configure("Pending", background="#FFFFFF")
        self._tree.tag_configure("Running", background="#FFF6D5")
        self._tree.tag_configure("Success", background="#D4EFDF")
        self._tree.tag_configure("Already Downloaded", background="#D6EAF8")
        self._tree.tag_configure("Failed Login", background="#FADBD8")
        self._tree.tag_configure("Wrong Password", background="#FADBD8")
        self._tree.tag_configure("CAPTCHA Failed", background="#FCF3CF")
        self._tree.tag_configure("Portal Error", background="#FADBD8")
        self._tree.tag_configure("Skipped", background="#EAEDED")

        scroll = ttk.Scrollbar(table_frame, orient="vertical",
                               command=self._tree.yview)
        self._tree.configure(yscrollcommand=scroll.set)
        self._tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")

        self._tree.bind("<Button-1>", self._on_tree_click)

        # ---------------- bottom: log + progress -----------------------------
        bot = ctk.CTkFrame(self)
        bot.grid(row=2, column=0, sticky="ew", padx=10, pady=10)
        bot.grid_columnconfigure(0, weight=1)

        self._progress = ctk.CTkProgressBar(bot, height=14)
        self._progress.set(0)
        self._progress.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        self._progress_label = ctk.CTkLabel(bot, text="Idle.")
        self._progress_label.grid(row=0, column=1, padx=10)

        self._log = ctk.CTkTextbox(bot, height=160,
                                   font=ctk.CTkFont(family="Consolas", size=11))
        self._log.grid(row=1, column=0, columnspan=2, sticky="ew",
                       padx=8, pady=(4, 8))
        self._log.configure(state="disabled")

        # Footer hint
        footer = ctk.CTkLabel(
            self,
            text=(f"Downloads → {config.DOWNLOADS_DIR}    "
                  f"Reports → {config.REPORTS_DIR}    "
                  f"Logs → {config.LOGS_DIR}"),
            text_color=("gray30", "gray70"),
        )
        footer.grid(row=3, column=0, sticky="ew", padx=12, pady=(0, 6))

    # ----------------------------------------------------------- handlers --

    def _on_load_excel(self) -> None:
        initial = str(config.ROOT_DIR)
        path = filedialog.askopenfilename(
            initialdir=initial,
            title="Select Clients Excel",
            filetypes=[("Excel Files", "*.xlsx *.xlsm *.xls")],
        )
        if not path:
            return
        try:
            clients = read_clients(Path(path))
        except Exception as exc:  # noqa: BLE001
            log.exception("excel read failed")
            self._toast(f"Could not read Excel: {exc}")
            return
        self._populate_table(clients)
        log.info("Loaded %d clients from %s", len(clients), path)

    def _populate_table(self, clients: list[Client]) -> None:
        for r in self._tree.get_children():
            self._tree.delete(r)
        self._row_to_client.clear()
        self._results_by_row.clear()
        self._clients = clients

        for c in clients:
            row_id = self._tree.insert(
                "", "end",
                values=("☑", c.sr_no, c.name, c.user_id, c.gstin, "Pending", ""),
                tags=("Pending",),
            )
            self._row_to_client[row_id] = c
        self._refresh_summary()

    def _on_tree_click(self, event) -> None:
        region = self._tree.identify("region", event.x, event.y)
        if region != "cell":
            return
        col = self._tree.identify_column(event.x)
        row_id = self._tree.identify_row(event.y)
        if not row_id or col != "#1":
            return
        # Toggle checkbox
        vals = list(self._tree.item(row_id, "values"))
        vals[0] = "☐" if vals[0] == "☑" else "☑"
        self._tree.item(row_id, values=vals)
        self._refresh_summary()

    def _select_all(self, on: bool) -> None:
        sym = "☑" if on else "☐"
        for r in self._tree.get_children():
            vals = list(self._tree.item(r, "values"))
            vals[0] = sym
            self._tree.item(r, values=vals)
        self._refresh_summary()

    def _selected_clients(self) -> list[Client]:
        out: list[Client] = []
        for r in self._tree.get_children():
            vals = self._tree.item(r, "values")
            if vals and vals[0] == "☑":
                client = self._row_to_client.get(r)
                if client:
                    out.append(client)
        return out

    def _refresh_summary(self) -> None:
        total = len(self._clients)
        sel = len(self._selected_clients())
        self._summary_label.configure(
            text=f"Loaded: {total}    Selected: {sel}"
        )

    # ------------------------------------------------------------- start --

    def _on_start(self) -> None:
        clients = self._selected_clients()
        if not clients:
            self._toast("Select at least one client.")
            return

        try:
            year = int(self._year_var.get())
        except ValueError:
            self._toast("Invalid year.")
            return
        from ..config import MONTH_NUMBER
        month = MONTH_NUMBER[self._month_var.get()]

        opts = BatchOptions(
            year=year, month=month,
            base_download_dir=config.DOWNLOADS_DIR,
            headless=bool(self._headless_var.get()),
            cancel_event=self._cancel_event,
        )
        self._cancel_event.clear()

        # Reset row statuses
        for r in self._tree.get_children():
            self._tree.set(r, "status", "Pending")
            self._tree.set(r, "msg", "")
            self._tree.item(r, tags=("Pending",))

        self._start_btn.configure(state="disabled")
        self._stop_btn.configure(state="normal")
        self._progress.set(0)
        self._progress_label.configure(text=f"Starting {len(clients)} clients...")

        self._worker = threading.Thread(
            target=self._run_worker, args=(clients, opts), daemon=True,
        )
        self._worker.start()

    def _on_stop(self) -> None:
        if self._worker and self._worker.is_alive():
            self._cancel_event.set()
            self._progress_label.configure(text="Stop requested...")

    def _run_worker(self, clients: list[Client], opts: BatchOptions) -> None:
        total = len(clients)
        done = [0]

        def status_cb(result: ClientResult) -> None:
            done[0] += 1
            self.after(0, self._apply_result_to_row, result, done[0], total)

        def manual_captcha_cb(image_bytes: bytes, attempt: int) -> Optional[str]:
            client_name = "(client)"
            return self._request_manual_captcha_from_thread(
                image_bytes, attempt, client_name
            )

        try:
            results, report_path = run_batch(
                clients, opts,
                on_status=status_cb,
                manual_captcha=manual_captcha_cb,
            )
            self.after(0, self._on_batch_done, results, report_path)
        except Exception as exc:  # noqa: BLE001
            log.exception("batch failed")
            self.after(0, self._on_batch_failed, str(exc))

    def _apply_result_to_row(self, result: ClientResult, done: int, total: int) -> None:
        # Find the row for this client by sr_no + gstin (unique)
        for r in self._tree.get_children():
            client = self._row_to_client.get(r)
            if not client:
                continue
            if client.sr_no == result.client.sr_no and client.gstin == result.client.gstin:
                self._tree.set(r, "status", result.status)
                msg = result.error_reason or (Path(result.file_path).name if result.file_path else "")
                self._tree.set(r, "msg", msg)
                self._tree.item(r, tags=(result.status,))
                self._results_by_row[r] = result
                break
        self._progress.set(done / total if total else 0)
        self._progress_label.configure(text=f"{done}/{total} done")

    def _on_batch_done(self, results: list[ClientResult], report_path: Path) -> None:
        self._start_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")
        ok = sum(1 for r in results if r.status in ("Success", "Already Downloaded"))
        self._progress_label.configure(
            text=f"Finished. {ok}/{len(results)} OK. Report: {report_path.name}"
        )
        log.info("Batch finished. Report: %s", report_path)

    def _on_batch_failed(self, msg: str) -> None:
        self._start_btn.configure(state="normal")
        self._stop_btn.configure(state="disabled")
        self._progress_label.configure(text=f"Failed: {msg}")

    # --------------------------------------------------- manual CAPTCHA --

    def _request_manual_captcha_from_thread(
        self, image_bytes: bytes, attempt: int, client_name: str
    ) -> Optional[str]:
        """Called from worker thread; ask main thread to show dialog and wait."""
        self._captcha_response = None
        self._captcha_request = (image_bytes, attempt, client_name)
        self._captcha_event.clear()
        # Wait up to 30 seconds for user
        ok = self._captcha_event.wait(timeout=30)
        if not ok:
            return None
        return self._captcha_response

    def _poll_captcha_request(self) -> None:
        if self._captcha_request is not None:
            image_bytes, attempt, client_name = self._captcha_request
            self._captcha_request = None
            value = prompt_manual_captcha(self, image_bytes, attempt, client_name)
            self._captcha_response = value
            self._captcha_event.set()
        self.after(200, self._poll_captcha_request)

    # ----------------------------------------------------------- log --

    def _poll_log_queue(self) -> None:
        flushed = 0
        try:
            while flushed < 50:
                line = self._gui_log_queue.get_nowait()
                self._append_log(line)
                flushed += 1
        except queue.Empty:
            pass
        self.after(200, self._poll_log_queue)

    def _append_log(self, line: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", line + "\n")
        # Trim to last 800 lines
        try:
            line_count = int(self._log.index("end-1c").split(".")[0])
            if line_count > 800:
                self._log.delete("1.0", f"{line_count - 800}.0")
        except Exception:
            pass
        self._log.see("end")
        self._log.configure(state="disabled")

    def _toast(self, message: str) -> None:
        log.warning(message)
        self._progress_label.configure(text=message)


def _default_month_name() -> str:
    """Default to last completed month."""
    today = datetime.now()
    if today.month == 1:
        m = 12
    else:
        m = today.month - 1
    names = ["January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December"]
    return names[m - 1]
