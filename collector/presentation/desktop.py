"""Desktop entry point; workspace pages share the application controller."""

from collector.application.desktop import DesktopController


def gui(controller: DesktopController, autostart=False, smoke_test=False):
    from .workbench import Workbench

    view = Workbench(controller, autostart, smoke_test)
    view.window.mainloop()
