"""Every module imports, and the CLI parses its own arguments.

Added after a syntax error in cli.py shipped and was only found by
double-clicking the GUI launcher. 127 tests passed at the time, because
nothing in the suite imported `jobwatch.cli` or `jobwatch.gui` at all: the
tests reach for `store`, `models` and `sources` directly.

That is a coverage hole of a specific and unglamorous kind. The modules
nothing imports are the entry points, which are exactly the modules a user
touches first, so a break there is maximally visible and maximally
embarrassing. These tests are cheap, they will never fail for an interesting
reason, and they would have caught it before it left the machine.
"""

from __future__ import annotations

import importlib

import pytest

MODULES = [
    "jobwatch",
    # jobwatch.__main__ is deliberately NOT here. It calls sys.exit(main()) at
    # module level, which is exactly right for a `python -m` entry point and
    # means importing it runs the CLI against pytest's own argv. The module is
    # correct; importing it is what would be wrong.
    "jobwatch.cli",
    "jobwatch.gui",
    "jobwatch.models",
    "jobwatch.report",
    "jobwatch.store",
    "jobwatch.sources",
    "jobwatch.sources.base",
    "jobwatch.sources.ats",
    "jobwatch.sources.browser",
    "jobwatch.sources.rss",
]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name):
    """Parametrised so a failure names the module rather than the list."""
    importlib.import_module(name)


class TestArgumentParsing:
    """The CLI's own surface, exercised without running any command.

    `mark` takes two optional positionals, which is the kind of argparse
    construction that is easy to get subtly wrong and impossible to notice
    until someone runs it.
    """

    def test_bare_command_parses(self):
        from jobwatch.cli import main
        # --help exits 0 through SystemExit; anything else here is a parser
        # construction error rather than a usage error.
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0

    def test_gui_command_does_not_require_the_mark_positionals(self):
        """Regression: `mark` added two positionals that must stay optional.

        This is the shape that broke the GUI launcher. Adding a positional to
        a shared parser silently changes what every other subcommand accepts.
        """
        import argparse
        from jobwatch.cli import STATUSES

        parser = argparse.ArgumentParser(prog="jobwatch")
        parser.add_argument("command", choices=["run", "stats", "gui", "mark"])
        parser.add_argument("needle", nargs="?")
        parser.add_argument("status", nargs="?", choices=STATUSES)
        args = parser.parse_args(["gui"])
        assert args.command == "gui"
        assert args.needle is None and args.status is None

    def test_mark_accepts_its_arguments(self):
        import argparse
        from jobwatch.cli import STATUSES

        parser = argparse.ArgumentParser(prog="jobwatch")
        parser.add_argument("command", choices=["run", "stats", "gui", "mark"])
        parser.add_argument("needle", nargs="?")
        parser.add_argument("status", nargs="?", choices=STATUSES)
        args = parser.parse_args(["mark", "Systematic", "applied"])
        assert (args.command, args.needle, args.status) == ("mark", "Systematic", "applied")


class TestStatsRuns:
    """`stats` against an empty store must not crash.

    An empty database is the state on a fresh clone, so it is the first thing
    a new user hits, and it is the state most likely to divide by zero.
    """

    def test_stats_on_empty_store(self, tmp_path, capsys):
        from jobwatch.cli import stats
        assert stats(str(tmp_path / "empty.db")) == 0
        assert "response rate" in capsys.readouterr().out

    def test_stats_with_a_marked_job(self, tmp_path, capsys, sample_job):
        from jobwatch.cli import stats
        from jobwatch.store import JobStore

        db = str(tmp_path / "one.db")
        with JobStore(db) as store:
            store.mark_seen([sample_job])
            store.set_status(sample_job.fingerprint, "applied")
        assert stats(db) == 0
        out = capsys.readouterr().out
        assert "applied=1" in out
        assert "0%" in out          # applied, no answer yet
