from ..common import ARROW_PURPLE_UV_OFFSET

def initialize():
    try:
        from game.entities import arrow_model
    except Exception:
        return
    if getattr(arrow_model.ArrowRendererMixin.draw, "_corruptor_patched", False):
        return
    original = arrow_model.ArrowRendererMixin.draw

    def draw(self):
        if not getattr(self, "is_corruptor_arrow", False):
            return original(self)
        old_v = arrow_model._arrow_texture_v

        def purple_v(image_y):
            return old_v(float(image_y) + ARROW_PURPLE_UV_OFFSET)

        arrow_model._arrow_texture_v = purple_v
        try:
            return original(self)
        finally:
            arrow_model._arrow_texture_v = old_v

    draw._corruptor_patched = True
    arrow_model.ArrowRendererMixin.draw = draw
