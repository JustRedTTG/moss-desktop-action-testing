from .common import INJECTOR_COLOR
from .menu import InjectorMenu
from gui import GUI
import pygameextra as pe


class Injector(pe.ChildContext):
    LAYER = pe.AFTER_LOOP_LAYER
    parent_context: GUI
    MIN_HOVER_T = 0.3
    MAX_HOVER_T = 2

    menu: InjectorMenu

    def __init__(self, parent: GUI):
        self.hover = False
        self.hover_t = self.MIN_HOVER_T
        self.last_area = None
        super().__init__(parent)
        self.menu = InjectorMenu(self)

    def loop(self):
        try:
            resync_rect = self.parent_context.ratios.pad_button_rect(self.parent_context.main_menu.resync_rect)
        except AttributeError:
            return
        area = resync_rect.copy()
        area.right -= self.width - resync_rect.right
        area.right -= area.width
        draw_area = area.copy()
        draw_area.scale_by_ip(self.hover_t, self.hover_t)
        draw_area.center = area.center
        self.last_area = draw_area
        pe.draw.rect(INJECTOR_COLOR, draw_area)
        pe.button.action(area, action_set={
            'hover': {
                'action': self.hover_hold
            },
            'hover_draw': None
        }, name='moss_extender')

    def post_loop(self):
        if self.hover:
            self.hover_t += self.parent_context.delta_time * 4 * self.MAX_HOVER_T
            if self.hover_t >= self.MAX_HOVER_T:
                self.hover_t = self.MAX_HOVER_T
            self.menu()
            self.hover = False
        elif self.hover_t > self.MIN_HOVER_T:
            self.hover_t -= self.parent_context.delta_time * 4 * self.MAX_HOVER_T
            if self.hover_t <= self.MIN_HOVER_T:
                self.hover_t = self.MIN_HOVER_T
            else:
                self.menu()

    def hover_hold(self):
        self.hover = True

    def run_pp_helpers(self):
        self()

    @property
    def t(self):
        return (self.hover_t - self.MIN_HOVER_T) / (self.MAX_HOVER_T - self.MIN_HOVER_T)