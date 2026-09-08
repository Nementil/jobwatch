"""A compact desktop UI.

    python -m jobwatch gui

Tkinter rather than a web UI or Qt, for one reason: it ships with CPython, so
the GUI adds no dependency and nothing extra to install. A job monitor that
needs a server started before you can read it will not get used.

Design constraints this had to satisfy:

* The collection runs on a worker thread. Tkinter is not thread safe, so the
  worker never touches a widget: it posts results back through a Queue that
  the UI polls. Calling into Tk from a worker appears to work and then
  crashes intermittently under load, which is the worst failure mode to ship.
* Results stay visible after the run. The point is to scan them, not to watch
  a progress bar.
* Nothing is written to the seen-set unless "Mark as seen" is pressed, so
  browsing results never silently suppresses them from a later run.
"""

from __future__ import annotations

import logging
import queue
import threading
import webbrowser
from datetime import date
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import messagebox, ttk

import yaml

from .models import Job
from .report import render_markdown
from .sources import filter_jobs
from .store import STATUSES, JobStore

log = logging.getLogger(__name__)


class JobWatchGUI:
    def __init__(self, config_path: str = "config.yaml", db_path: str = "jobwatch.db") -> None:
        self.config_path = Path(config_path)
        self.db_path = db_path
        self.config: dict[str, Any] = {}
        self.results: list[Job] = []
        self.queue: queue.Queue = queue.Queue()

        self.root = tk.Tk()
        self.root.title("jobwatch")
        self.root.geometry("980x620")
        self.root.minsize(720, 420)

        self._build_widgets()
        self._load_config()
        self.root.after(100, self._drain_queue)

    # ---------------------------------------------------------------- UI --
    def _build_widgets(self) -> None:
        root = self.root
        root.columnconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        # -- search controls --
        controls = ttk.LabelFrame(root, text="Search", padding=8)
        controls.grid(row=0, column=0, sticky="ew", padx=8, pady=(8, 4))
        controls.columnconfigure(1, weight=1)

        ttk.Label(controls, text="Keywords").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.keywords_var = tk.StringVar()
        ttk.Entry(controls, textvariable=self.keywords_var).grid(row=0, column=1, sticky="ew")

        ttk.Label(controls, text="Locations").grid(row=1, column=0, sticky="w", padx=(0, 6), pady=(4, 0))
        self.locations_var = tk.StringVar()
        ttk.Entry(controls, textvariable=self.locations_var).grid(row=1, column=1, sticky="ew", pady=(4, 0))

        ttk.Label(
            controls,
            text="Comma separated. Empty keywords means everything; empty locations means anywhere.",
            foreground="#666",
        ).grid(row=2, column=1, sticky="w", pady=(2, 0))

        # -- actions --
        actions = ttk.Frame(root, padding=(8, 0))
        actions.grid(row=1, column=0, sticky="ew")

        self.run_btn = ttk.Button(actions, text="Search", command=self.on_search)
        self.run_btn.pack(side="left")

        self.new_only = tk.BooleanVar(value=True)
        ttk.Checkbutton(actions, text="New only", variable=self.new_only).pack(side="left", padx=(8, 0))

        self.mark_btn = ttk.Button(actions, text="Mark as seen", command=self.on_mark_seen, state="disabled")
        self.mark_btn.pack(side="left", padx=(8, 0))

        self.export_btn = ttk.Button(actions, text="Export report", command=self.on_export, state="disabled")
        self.export_btn.pack(side="left", padx=(8, 0))

        self.progress = ttk.Progressbar(actions, mode="indeterminate", length=140)
        self.progress.pack(side="right")

        # -- results --
        wrap = ttk.Frame(root, padding=8)
        wrap.grid(row=2, column=0, sticky="nsew")
        wrap.columnconfigure(0, weight=1)
        wrap.rowconfigure(0, weight=1)

        # "status" is the APPLICATION status (new/applied/rejected/...), not
        # the seen-set flag and not the status bar. Three things called status
        # in one file is a real risk here, so the seen-set is only ever
        # referred to as "seen" and the bar as "status_text".
        columns = ("status", "company", "title", "location", "source", "posted")
        self.tree = ttk.Treeview(wrap, columns=columns, show="headings", selectmode="browse")
        for col, label, width in (
            ("status", "Status", 80),
            ("company", "Company", 170),
            ("title", "Title", 380),
            ("location", "Location", 130),
            ("source", "Source", 150),
            ("posted", "Posted", 90),
        ):
            self.tree.heading(col, text=label, command=lambda c=col: self._sort_by(c))
            self.tree.column(col, width=width, anchor="w")
        self.tree.grid(row=0, column=0, sticky="nsew")

        bar = ttk.Scrollbar(wrap, orient="vertical", command=self.tree.yview)
        bar.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=bar.set)

        # Double-click and Enter both open, because people reach for both.
        self.tree.bind("<Double-1>", self.on_open)
        self.tree.bind("<Return>", self.on_open)

        # -- application tracking --
        #
        # Directly under the results rather than in a dialog, because this has
        # to be usable in the same pass as reading the list. Tracking that
        # costs a context switch is tracking that gets abandoned in a week,
        # and an abandoned tracker produces a response rate that is worse than
        # no response rate: it looks like data and is not.
        track = ttk.LabelFrame(root, text="Selected job", padding=8)
        track.grid(row=3, column=0, sticky="ew", padx=8, pady=(0, 4))
        track.columnconfigure(3, weight=1)

        ttk.Label(track, text="Mark as:").grid(row=0, column=0, sticky="w")

        self.mark_status = tk.StringVar(value="applied")
        self.status_box = ttk.Combobox(
            track, textvariable=self.mark_status, values=list(STATUSES),
            state="readonly", width=12,
        )
        self.status_box.grid(row=0, column=1, sticky="w", padx=(6, 10))

        ttk.Label(track, text="Note:").grid(row=0, column=2, sticky="w")
        self.note_var = tk.StringVar()
        ttk.Entry(track, textvariable=self.note_var).grid(
            row=0, column=3, sticky="ew", padx=(6, 10)
        )

        self.apply_btn = ttk.Button(
            track, text="Save", command=self.on_set_status, state="disabled"
        )
        self.apply_btn.grid(row=0, column=4, sticky="e")

        # Enabling this only on selection stops the most likely mistake, which
        # is pressing Save with nothing selected and silently marking nothing.
        self.tree.bind("<<TreeviewSelect>>", self._on_select)

        self.status_text = tk.StringVar(value="Ready.")
        ttk.Label(root, textvariable=self.status_text, relief="sunken", anchor="w", padding=4).grid(
            row=4, column=0, sticky="ew"
        )

    # ------------------------------------------------------------ config --
    def _load_config(self) -> None:
        if not self.config_path.exists():
            self.status_text.set(f"No {self.config_path}. Copy config.example.yaml first.")
            self.run_btn.state(["disabled"])
            return
        self.config = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
        self.keywords_var.set(", ".join(self.config.get("keywords", [])))
        self.locations_var.set(", ".join(self.config.get("locations", [])))
        enabled = [s for s in self.config.get("sources", []) if s.get("enabled", True)]
        self.status_text.set(f"Loaded {self.config_path.name}: {len(enabled)} source(s) enabled.")

    @staticmethod
    def _split(text: str) -> list[str]:
        return [part.strip() for part in text.split(",") if part.strip()]

    # ------------------------------------------------------------ search --
    def on_search(self) -> None:
        self.run_btn.state(["disabled"])
        self.mark_btn.state(["disabled"])
        self.export_btn.state(["disabled"])
        self.progress.start(12)
        self.status_text.set("Searching...")
        self.tree.delete(*self.tree.get_children())
        self.results = []

        # Config is read on the UI thread and passed in, so the worker never
        # touches shared mutable state or a widget.
        threading.Thread(
            target=self._worker,
            args=(dict(self.config), self._split(self.keywords_var.get()),
                  self._split(self.locations_var.get()), self.new_only.get()),
            daemon=True,
        ).start()

    def _worker(self, config: dict, keywords: list[str], locations: list[str], new_only: bool) -> None:
        """Runs off the UI thread. Must never touch a widget."""
        try:
            from .cli import build_sources

            collected: list[Job] = []
            for source in build_sources(config):
                collected.extend(source.collect())
            matched = filter_jobs(collected, keywords, locations)
            if new_only:
                with JobStore(self.db_path) as store:
                    matched = store.new_jobs(matched)
            self.queue.put(("done", matched, len(collected)))
        except Exception as exc:            # noqa: BLE001 - reported to the user
            log.exception("search failed")
            self.queue.put(("error", exc, 0))

    def _drain_queue(self) -> None:
        """Polled on the UI thread. The only place results reach a widget."""
        try:
            while True:
                kind, payload, total = self.queue.get_nowait()
                if kind == "done":
                    self._show_results(payload, total)
                elif kind == "error":
                    self.progress.stop()
                    self.run_btn.state(["!disabled"])
                    self.status_text.set(f"Failed: {payload}")
                    messagebox.showerror("jobwatch", f"Search failed:\n{payload}")
        except queue.Empty:
            pass
        self.root.after(100, self._drain_queue)

    def _statuses_for(self, jobs: list[Job]) -> dict[str, str]:
        """Current application status per fingerprint, for jobs already stored.

        One query for the whole result set rather than one per row: a search
        can return a hundred jobs and the UI thread is doing this.

        A job that has never been recorded has no row at all, which is not the
        same as `new` and is shown differently. Conflating them would claim a
        vacancy is on the worklist when nothing has been written about it.
        """
        if not jobs:
            return {}
        with JobStore(self.db_path) as store:
            known = {row["fingerprint"]: row["status"] for row in store.all_jobs()}
        return {j.fingerprint: known.get(j.fingerprint, "") for j in jobs}

    def _show_results(self, jobs: list[Job], total_seen: int) -> None:
        self.progress.stop()
        self.run_btn.state(["!disabled"])
        self.results = jobs
        statuses = self._statuses_for(jobs)
        for job in jobs:
            self.tree.insert(
                "", "end",
                values=(statuses.get(job.fingerprint) or "-",
                        job.company, job.title, job.location or "-",
                        job.source, job.posted.isoformat() if job.posted else "-"),
            )
        if jobs:
            self.mark_btn.state(["!disabled"])
            self.export_btn.state(["!disabled"])
        scope = "new" if self.new_only.get() else "matching"
        self.status_text.set(
            f"{len(jobs)} {scope} of {total_seen} collected. "
            f"Double-click to open. {self._rate_summary()}"
        )

    def _rate_summary(self) -> str:
        """The response rate, phrased so zero applications is not a bare 0%.

        A rate of 0 from no applications and a rate of 0 from thirty are the
        same number and completely different situations, and only one of them
        means something is wrong.
        """
        with JobStore(self.db_path) as store:
            applied, answered, rate = store.response_rate()
        if not applied:
            return "No applications tracked yet."
        return f"Responses: {answered}/{applied} ({rate:.0%})."

    # ------------------------------------------------------------ actions --
    def _selected_job(self) -> Job | None:
        selection = self.tree.selection()
        if not selection:
            return None
        index = self.tree.index(selection[0])
        return self.results[index] if 0 <= index < len(self.results) else None

    def _on_select(self, _event: object = None) -> None:
        """Enable Save and show what the selected job is currently marked as."""
        job = self._selected_job()
        if not job:
            self.apply_btn.state(["disabled"])
            return
        self.apply_btn.state(["!disabled"])
        with JobStore(self.db_path) as store:
            rows = store.find(job.fingerprint)
        if rows:
            # Pre-select the CURRENT status rather than leaving the dropdown on
            # whatever was used last. Otherwise the box shows "applied" next to
            # a job that was rejected, which is a UI actively lying about state.
            self.mark_status.set(rows[0]["status"])
            self.note_var.set(rows[0]["note"] or "")
        else:
            self.note_var.set("")

    def on_set_status(self) -> None:
        """Record the selected job's application status.

        Writes the job to the store first if it is not there yet. Marking a
        job you just found as `applied` has to work without pressing "Mark as
        seen" first: those are unrelated ideas, and requiring one before the
        other would be a rule nobody can guess.
        """
        job = self._selected_job()
        if not job:
            return
        status = self.mark_status.get()
        with JobStore(self.db_path) as store:
            if not store.is_seen(job):
                store.mark_seen([job])
            store.set_status(job.fingerprint, status, self.note_var.get().strip())

        selection = self.tree.selection()
        if selection:
            self.tree.set(selection[0], "status", status)
        self.status_text.set(
            f"{job.company}: {job.title} marked {status}. {self._rate_summary()}"
        )

    def on_open(self, _event: object = None) -> None:
        job = self._selected_job()
        if job:
            webbrowser.open(job.url)

    def on_mark_seen(self) -> None:
        if not self.results:
            return
        if not messagebox.askyesno(
            "jobwatch",
            f"Mark {len(self.results)} job(s) as seen?\n\n"
            "They will not appear in future 'new only' searches.",
        ):
            return
        with JobStore(self.db_path) as store:
            store.mark_seen(self.results)
        self.mark_btn.state(["disabled"])
        self.status_text.set(f"Marked {len(self.results)} job(s) as seen.")

    def on_export(self) -> None:
        out = Path(self.config.get("report_path", "reports"))
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{date.today().isoformat()}.md"
        path.write_text(render_markdown(self.results, date.today()), encoding="utf-8")
        self.status_text.set(f"Wrote {path}")

    def _sort_by(self, column: str) -> None:
        """Sort the visible rows and keep self.results aligned with them.

        The two must stay in step: row order is how a selected row is mapped
        back to a Job, so sorting the tree without sorting results would open
        the wrong vacancy.
        """
        # Derived from the tree's own column order, NOT a second hardcoded map.
        # There was one here, and inserting the Status column in front silently
        # shifted every entry by one: clicking "Company" sorted by status and
        # clicking "Status" raised KeyError. A duplicated ordering is a fact
        # stated twice, and the copy that is not executed is the one that rots.
        index = list(self.tree["columns"]).index(column)
        pairs = sorted(
            zip(self.tree.get_children(""), self.results),
            key=lambda pair: str(self.tree.item(pair[0], "values")[index]).lower(),
        )
        self.results = [job for _, job in pairs]
        for position, (item, _) in enumerate(pairs):
            self.tree.move(item, "", position)

    def run(self) -> None:
        self.root.mainloop()


#: Written next to the launcher, which is where someone who just
#: double-clicked it will think to look.
STARTUP_LOG = Path(__file__).resolve().parents[2] / "jobwatch-startup.log"


def main(config_path: str = "config.yaml", db_path: str = "jobwatch.db") -> int:
    """Start the GUI, and leave evidence behind if it cannot start.

    The launcher runs this under pythonw.exe, which has no console. Anything
    raised here would otherwise be written to a handle nobody is reading, and
    the user sees a double-click that does nothing at all: the least
    debuggable failure a desktop app can have.

    The .bat pre-flights the import with the console interpreter and catches
    that class of failure already. This catches the rest, which is everything
    between "the module imported" and "a window is on screen": a malformed
    config, a missing tkinter, a display that will not initialise.

    Re-raised after logging rather than swallowed. Swallowing would make the
    exit code a lie, and this function is called from the CLI too, where a
    non-zero return is the only signal the caller has.
    """
    try:
        JobWatchGUI(config_path, db_path).run()
    except Exception:
        try:
            stamp = datetime.now().isoformat(timespec="seconds")
            STARTUP_LOG.write_text(
                "\n".join([
                    f"jobwatch failed to start at {stamp}",
                    "",
                    traceback.format_exc(),
                ]),
                encoding="utf-8",
            )
        except OSError:
            # A read-only or missing directory must not replace the real
            # traceback with a second, less interesting one.
            pass
        raise
    return 0
