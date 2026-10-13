from __future__ import annotations

import unittest
from queue import SimpleQueue
from types import SimpleNamespace
from unittest.mock import Mock, patch

from skin_analysis.gui import RawDataViewerApp


class ClassicThreadHandoffTests(unittest.TestCase):
    def _fake_app(self):
        return SimpleNamespace(
            _classic_results=SimpleQueue(),
            _plot_data_apply=Mock(),
            _plot_data_failed=Mock(),
            after=Mock(side_effect=AssertionError("Tk after must not be called from a worker")),
        )

    def test_plot_worker_only_enqueues_success_without_touching_tk(self) -> None:
        app = self._fake_app()
        payload = object()
        with patch("skin_analysis.gui.build_plot_payload", return_value=payload):
            RawDataViewerApp._plot_data_worker(app, settings=object(), group_color=None)

        app.after.assert_not_called()
        callback, args = app._classic_results.get_nowait()
        self.assertIs(callback, app._plot_data_apply)
        self.assertEqual(args, (payload,))
        callback(*args)
        app._plot_data_apply.assert_called_once_with(payload)

    def test_plot_worker_only_enqueues_failure_without_touching_tk(self) -> None:
        app = self._fake_app()
        with patch("skin_analysis.gui.build_plot_payload", side_effect=ValueError("invalid file")):
            RawDataViewerApp._plot_data_worker(app, settings=object(), group_color=None)

        app.after.assert_not_called()
        callback, args = app._classic_results.get_nowait()
        self.assertIs(callback, app._plot_data_failed)
        self.assertIsInstance(args[0], ValueError)

    def test_main_thread_poll_drains_results_then_reschedules_itself(self) -> None:
        queue = SimpleQueue()
        handler = Mock()
        queue.put((handler, ("sample",)))
        app = SimpleNamespace(
            _classic_results=queue,
            after=Mock(),
            winfo_exists=lambda: True,
            _poll_classic_results=Mock(),
        )
        RawDataViewerApp._poll_classic_results(app)

        handler.assert_called_once_with("sample")
        self.assertTrue(queue.empty())
        app.after.assert_called_once_with(80, app._poll_classic_results)

    def test_destroyed_gui_does_not_schedule_more_polling(self) -> None:
        app = SimpleNamespace(
            _classic_results=SimpleQueue(),
            after=Mock(),
            winfo_exists=lambda: False,
        )
        RawDataViewerApp._poll_classic_results(app)
        app.after.assert_not_called()


if __name__ == "__main__":
    unittest.main()
