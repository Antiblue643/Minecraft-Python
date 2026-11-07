from __future__ import annotations

import math
from typing import Optional, Sequence

from OpenGL.GL import (
    GL_ALPHA_TEST,
    GL_BLEND,
    GL_CULL_FACE,
    GL_DEPTH_TEST,
    GL_EQUAL,
    GL_LEQUAL,
    GL_NORMALIZE,
    GL_ONE_MINUS_SRC_ALPHA,
    GL_SRC_ALPHA,
    GL_TEXTURE_2D,
    glBlendFunc,
    glColor4f,
    glDepthFunc,
    glDisable,
    glEnable,
    glPopMatrix,
    glPushMatrix,
    glRotatef,
    glScalef,
    glTranslatef,
)

from engine.legacy_lighting import (
    disable_standard_item_lighting,
    enable_standard_item_lighting,
)
from game.entities.player_model import (
    ModelBiped,
    ModelPart,
    _UNIT,
    _bind_skin,
    resolve_skin_texture,
)

SPAWNER_DISPLAY_PITCH = 30.0

class ModelQuadruped:

    def __init__(self, leg_height: int = 6, inflate: float = 0.0):
        self.head = ModelPart(0, 0, -4, -4, -8, 8, 8, 8, inflate=inflate)
        self.head.set_rotation_point(0.0, float(18 - leg_height), -6.0)

        self.body = ModelPart(28, 8, -5, -10, -7, 10, 16, 8, inflate=inflate)
        self.body.set_rotation_point(0.0, float(17 - leg_height), 2.0)

        self.leg1 = ModelPart(0, 16, -2, 0, -2, 4, leg_height, 4, inflate=inflate)
        self.leg1.set_rotation_point(-3.0, float(24 - leg_height), 7.0)
        self.leg2 = ModelPart(0, 16, -2, 0, -2, 4, leg_height, 4, inflate=inflate)
        self.leg2.set_rotation_point(3.0, float(24 - leg_height), 7.0)
        self.leg3 = ModelPart(0, 16, -2, 0, -2, 4, leg_height, 4, inflate=inflate)
        self.leg3.set_rotation_point(-3.0, float(24 - leg_height), -5.0)
        self.leg4 = ModelPart(0, 16, -2, 0, -2, 4, leg_height, 4, inflate=inflate)
        self.leg4.set_rotation_point(3.0, float(24 - leg_height), -5.0)

        self.parts = (self.head, self.body, self.leg1, self.leg2, self.leg3, self.leg4)

    def set_rotation_angles(
        self,
        limb_swing: float,
        limb_amount: float,
        age_in_ticks: float,
        net_head_yaw: float,
        head_pitch: float,
    ) -> None:
        self.head.rotate_angle_y = math.radians(net_head_yaw)
        self.head.rotate_angle_x = math.radians(head_pitch)
        self.body.rotate_angle_x = math.pi * 0.5
        self.leg1.rotate_angle_x = math.cos(limb_swing * 0.6662) * 1.4 * limb_amount
        self.leg2.rotate_angle_x = math.cos(limb_swing * 0.6662 + math.pi) * 1.4 * limb_amount
        self.leg3.rotate_angle_x = math.cos(limb_swing * 0.6662 + math.pi) * 1.4 * limb_amount
        self.leg4.rotate_angle_x = math.cos(limb_swing * 0.6662) * 1.4 * limb_amount

    def render(
        self,
        limb_swing: float,
        limb_amount: float,
        age_in_ticks: float,
        net_head_yaw: float,
        head_pitch: float,
        scale: float = _UNIT,
    ) -> None:
        self.set_rotation_angles(
            limb_swing, limb_amount, age_in_ticks, net_head_yaw, head_pitch
        )
        for part in self.parts:
            part.render(scale)


class ModelPig(ModelQuadruped):
    def __init__(self, inflate: float = 0.0):
        super().__init__(6, inflate)


class ModelSheep(ModelQuadruped):
    def __init__(self):
        super().__init__(12, 0.0)
        self.head = ModelPart(0, 0, -3, -4, -6, 6, 6, 8, inflate=0.0)
        self.head.set_rotation_point(0.0, 6.0, -8.0)
        self.body = ModelPart(28, 8, -4, -10, -7, 8, 16, 6, inflate=0.0)
        self.body.set_rotation_point(0.0, 5.0, 2.0)
        self.parts = (self.head, self.body, self.leg1, self.leg2, self.leg3, self.leg4)


class ModelCow(ModelQuadruped):

    def __init__(self, inflate: float = 0.0):
        super().__init__(12, inflate)
        self.head = ModelPart(0, 0, -4, -4, -6, 8, 8, 6, inflate=inflate)
        self.head.set_rotation_point(0.0, 4.0, -8.0)
        self.horn1 = ModelPart(22, 0, -5, -6, -3, 1, 3, 1, inflate=inflate)
        self.horn1.set_rotation_point(0.0, 4.0, -8.0)
        self.horn2 = ModelPart(22, 0, 4, -6, -3, 1, 3, 1, inflate=inflate)
        self.horn2.set_rotation_point(0.0, 4.0, -8.0)
        self.udders = ModelPart(52, 0, -2, -3, 0, 4, 6, 2, inflate=inflate)
        self.udders.set_rotation_point(0.0, 14.0, 6.0)
        self.udders.rotate_angle_x = math.pi * 0.5
        self.body = ModelPart(18, 4, -6, -10, -7, 12, 18, 10, inflate=inflate)
        self.body.set_rotation_point(0.0, 5.0, 2.0)
        self.leg1.rotation_point = (
            self.leg1.rotation_point[0] - 1.0,
            self.leg1.rotation_point[1],
            self.leg1.rotation_point[2],
        )
        self.leg2.rotation_point = (
            self.leg2.rotation_point[0] + 1.0,
            self.leg2.rotation_point[1],
            self.leg2.rotation_point[2],
        )
        self.leg3.rotation_point = (
            self.leg3.rotation_point[0] - 1.0,
            self.leg3.rotation_point[1],
            self.leg3.rotation_point[2] - 1.0,
        )
        self.leg4.rotation_point = (
            self.leg4.rotation_point[0] + 1.0,
            self.leg4.rotation_point[1],
            self.leg4.rotation_point[2] - 1.0,
        )
        self.parts = (self.head, self.body, self.leg1, self.leg2, self.leg3, self.leg4)

    def set_rotation_angles(
        self,
        limb_swing: float,
        limb_amount: float,
        age_in_ticks: float,
        net_head_yaw: float,
        head_pitch: float,
    ) -> None:
        super().set_rotation_angles(
            limb_swing, limb_amount, age_in_ticks, net_head_yaw, head_pitch
        )
        self.horn1.rotate_angle_y = self.head.rotate_angle_y
        self.horn1.rotate_angle_x = self.head.rotate_angle_x
        self.horn1.rotate_angle_z = self.head.rotate_angle_z
        self.horn2.rotate_angle_y = self.head.rotate_angle_y
        self.horn2.rotate_angle_x = self.head.rotate_angle_x
        self.horn2.rotate_angle_z = self.head.rotate_angle_z

    def render(
        self,
        limb_swing: float,
        limb_amount: float,
        age_in_ticks: float,
        net_head_yaw: float,
        head_pitch: float,
        scale: float = _UNIT,
    ) -> None:
        self.set_rotation_angles(
            limb_swing, limb_amount, age_in_ticks, net_head_yaw, head_pitch
        )
        for part in self.parts:
            part.render(scale)
        self.horn1.render(scale)
        self.horn2.render(scale)
        self.udders.render(scale)


class ModelSheepFur(ModelQuadruped):
    def __init__(self):
        super().__init__(12, 0.0)
        self.head = ModelPart(0, 0, -3, -4, -4, 6, 6, 6, inflate=0.6)
        self.head.set_rotation_point(0.0, 6.0, -8.0)
        self.body = ModelPart(28, 8, -4, -10, -7, 8, 16, 6, inflate=1.75)
        self.body.set_rotation_point(0.0, 5.0, 2.0)
        half = 0.5
        self.leg1 = ModelPart(0, 16, -2, 0, -2, 4, 6, 4, inflate=half)
        self.leg1.set_rotation_point(-3.0, 12.0, 7.0)
        self.leg2 = ModelPart(0, 16, -2, 0, -2, 4, 6, 4, inflate=half)
        self.leg2.set_rotation_point(3.0, 12.0, 7.0)
        self.leg3 = ModelPart(0, 16, -2, 0, -2, 4, 6, 4, inflate=half)
        self.leg3.set_rotation_point(-3.0, 12.0, -5.0)
        self.leg4 = ModelPart(0, 16, -2, 0, -2, 4, 6, 4, inflate=half)
        self.leg4.set_rotation_point(3.0, 12.0, -5.0)
        self.parts = (self.head, self.body, self.leg1, self.leg2, self.leg3, self.leg4)


class ModelChicken:

    def __init__(self):
        self.head = ModelPart(0, 0, -2, -6, -2, 4, 6, 3)
        self.head.set_rotation_point(0.0, 15.0, -4.0)

        self.bill = ModelPart(14, 0, -2, -4, -4, 4, 2, 2)
        self.bill.set_rotation_point(0.0, 15.0, -4.0)
        self.chin = ModelPart(14, 4, -1, -2, -3, 2, 2, 2)
        self.chin.set_rotation_point(0.0, 15.0, -4.0)

        self.body = ModelPart(0, 9, -3, -4, -3, 6, 8, 6)
        self.body.set_rotation_point(0.0, 16.0, 0.0)

        self.right_leg = ModelPart(26, 0, -1, 0, -3, 3, 5, 3)
        self.right_leg.set_rotation_point(-2.0, 19.0, 1.0)
        self.left_leg = ModelPart(26, 0, -1, 0, -3, 3, 5, 3)
        self.left_leg.set_rotation_point(1.0, 19.0, 1.0)

        self.right_wing = ModelPart(24, 13, 0, 0, -3, 1, 4, 6)
        self.right_wing.set_rotation_point(-4.0, 13.0, 0.0)
        self.left_wing = ModelPart(24, 13, -1, 0, -3, 1, 4, 6)
        self.left_wing.set_rotation_point(4.0, 13.0, 0.0)

        self.parts = (
            self.head, self.bill, self.chin, self.body,
            self.right_leg, self.left_leg, self.right_wing, self.left_wing,
        )

    def set_rotation_angles(
        self,
        limb_swing: float,
        limb_amount: float,
        wing_rotation: float,
        net_head_yaw: float,
        head_pitch: float,
    ) -> None:
        self.head.rotate_angle_x = math.radians(head_pitch)
        self.head.rotate_angle_y = math.radians(net_head_yaw)
        self.bill.rotate_angle_x = self.head.rotate_angle_x
        self.bill.rotate_angle_y = self.head.rotate_angle_y
        self.chin.rotate_angle_x = self.head.rotate_angle_x
        self.chin.rotate_angle_y = self.head.rotate_angle_y
        self.body.rotate_angle_x = math.pi * 0.5
        self.right_leg.rotate_angle_x = math.cos(limb_swing * 0.6662) * 1.4 * limb_amount
        self.left_leg.rotate_angle_x = math.cos(limb_swing * 0.6662 + math.pi) * 1.4 * limb_amount
        self.right_wing.rotate_angle_z = wing_rotation
        self.left_wing.rotate_angle_z = -wing_rotation

    def render(
        self,
        limb_swing: float,
        limb_amount: float,
        wing_rotation: float,
        net_head_yaw: float,
        head_pitch: float,
        scale: float = _UNIT,
    ) -> None:
        self.set_rotation_angles(
            limb_swing, limb_amount, wing_rotation, net_head_yaw, head_pitch
        )
        for part in self.parts:
            part.render(scale)


class ModelCreeper:
    def __init__(self):
        y = 4.0
        self.head = ModelPart(0, 0, -4, -8, -4, 8, 8, 8)
        self.head.set_rotation_point(0.0, y, 0.0)
        self.headwear = ModelPart(32, 0, -4, -8, -4, 8, 8, 8, inflate=0.5)
        self.headwear.set_rotation_point(0.0, y, 0.0)
        self.body = ModelPart(16, 16, -4, 0, -2, 8, 12, 4)
        self.body.set_rotation_point(0.0, y, 0.0)
        self.leg1 = ModelPart(0, 16, -2, 0, -2, 4, 6, 4)
        self.leg1.set_rotation_point(-2.0, 12.0 + y, 4.0)
        self.leg2 = ModelPart(0, 16, -2, 0, -2, 4, 6, 4)
        self.leg2.set_rotation_point(2.0, 12.0 + y, 4.0)
        self.leg3 = ModelPart(0, 16, -2, 0, -2, 4, 6, 4)
        self.leg3.set_rotation_point(-2.0, 12.0 + y, -4.0)
        self.leg4 = ModelPart(0, 16, -2, 0, -2, 4, 6, 4)
        self.leg4.set_rotation_point(2.0, 12.0 + y, -4.0)
        self.parts = (self.head, self.body, self.leg1, self.leg2, self.leg3, self.leg4)

    def set_rotation_angles(
        self,
        limb_swing: float,
        limb_amount: float,
        age_in_ticks: float,
        net_head_yaw: float,
        head_pitch: float,
    ) -> None:
        self.head.rotate_angle_y = math.radians(net_head_yaw)
        self.head.rotate_angle_x = math.radians(head_pitch)
        self.leg1.rotate_angle_x = math.cos(limb_swing * 0.6662) * 1.4 * limb_amount
        self.leg2.rotate_angle_x = math.cos(limb_swing * 0.6662 + math.pi) * 1.4 * limb_amount
        self.leg3.rotate_angle_x = math.cos(limb_swing * 0.6662 + math.pi) * 1.4 * limb_amount
        self.leg4.rotate_angle_x = math.cos(limb_swing * 0.6662) * 1.4 * limb_amount

    def render(
        self,
        limb_swing: float,
        limb_amount: float,
        age_in_ticks: float,
        net_head_yaw: float,
        head_pitch: float,
        scale: float = _UNIT,
    ) -> None:
        self.set_rotation_angles(
            limb_swing, limb_amount, age_in_ticks, net_head_yaw, head_pitch
        )
        for part in self.parts:
            part.render(scale)

class ModelSpider:
    def __init__(self):
        y = 15.0
        self.head = ModelPart(32, 4, -4, -4, -8, 8, 8, 8)
        self.head.set_rotation_point(0.0, y, -3.0)
        self.neck = ModelPart(0, 0, -3, -3, -3, 6, 6, 6)
        self.neck.set_rotation_point(0.0, y, 0.0)
        self.body = ModelPart(0, 12, -5, -4, -6, 10, 8, 12)
        self.body.set_rotation_point(0.0, y, 9.0)

        def leg(px, pz):
            part = ModelPart(18, 0, -15, -1, -1, 16, 2, 2)
            part.set_rotation_point(px, y, pz)
            return part

        def leg_r(px, pz):
            part = ModelPart(18, 0, -1, -1, -1, 16, 2, 2)
            part.set_rotation_point(px, y, pz)
            return part

        self.leg1 = leg(-4.0, 2.0)
        self.leg2 = leg_r(4.0, 2.0)
        self.leg3 = leg(-4.0, 1.0)
        self.leg4 = leg_r(4.0, 1.0)
        self.leg5 = leg(-4.0, 0.0)
        self.leg6 = leg_r(4.0, 0.0)
        self.leg7 = leg(-4.0, -1.0)
        self.leg8 = leg_r(4.0, -1.0)
        self.legs = (
            self.leg1, self.leg2, self.leg3, self.leg4,
            self.leg5, self.leg6, self.leg7, self.leg8,
        )
        self.parts = (self.head, self.neck, self.body) + self.legs

    def set_rotation_angles(
        self,
        limb_swing: float,
        limb_amount: float,
        age_in_ticks: float,
        net_head_yaw: float,
        head_pitch: float,
    ) -> None:
        self.head.rotate_angle_y = math.radians(net_head_yaw)
        self.head.rotate_angle_x = math.radians(head_pitch)

        quarter = math.pi * 0.25
        self.leg1.rotate_angle_z = -quarter
        self.leg2.rotate_angle_z = quarter
        self.leg3.rotate_angle_z = -quarter * 0.74
        self.leg4.rotate_angle_z = quarter * 0.74
        self.leg5.rotate_angle_z = -quarter * 0.74
        self.leg6.rotate_angle_z = quarter * 0.74
        self.leg7.rotate_angle_z = -quarter
        self.leg8.rotate_angle_z = quarter

        eighth = math.pi * 0.125
        self.leg1.rotate_angle_y = eighth * 2.0
        self.leg2.rotate_angle_y = -eighth * 2.0
        self.leg3.rotate_angle_y = eighth
        self.leg4.rotate_angle_y = -eighth
        self.leg5.rotate_angle_y = -eighth
        self.leg6.rotate_angle_y = eighth
        self.leg7.rotate_angle_y = -eighth * 2.0
        self.leg8.rotate_angle_y = eighth * 2.0

        def cos_phase(phase: float) -> float:
            return -(math.cos(limb_swing * 0.6662 * 2.0 + phase) * 0.4) * limb_amount

        def sin_phase(phase: float) -> float:
            return abs(math.sin(limb_swing * 0.6662 + phase) * 0.4) * limb_amount

        y0, y1 = cos_phase(0.0), cos_phase(math.pi)
        y2, y3 = cos_phase(math.pi * 0.5), cos_phase(math.pi * 1.5)
        z0, z1 = sin_phase(0.0), sin_phase(math.pi)
        z2, z3 = sin_phase(math.pi * 0.5), sin_phase(math.pi * 1.5)

        self.leg1.rotate_angle_y += y0
        self.leg2.rotate_angle_y += -y0
        self.leg3.rotate_angle_y += y1
        self.leg4.rotate_angle_y += -y1
        self.leg5.rotate_angle_y += y2
        self.leg6.rotate_angle_y += -y2
        self.leg7.rotate_angle_y += y3
        self.leg8.rotate_angle_y += -y3

        self.leg1.rotate_angle_z += z0
        self.leg2.rotate_angle_z += -z0
        self.leg3.rotate_angle_z += z1
        self.leg4.rotate_angle_z += -z1
        self.leg5.rotate_angle_z += z2
        self.leg6.rotate_angle_z += -z2
        self.leg7.rotate_angle_z += z3
        self.leg8.rotate_angle_z += -z3

    def render(
        self,
        limb_swing: float,
        limb_amount: float,
        age_in_ticks: float,
        net_head_yaw: float,
        head_pitch: float,
        scale: float = _UNIT,
    ) -> None:
        self.set_rotation_angles(
            limb_swing, limb_amount, age_in_ticks, net_head_yaw, head_pitch
        )
        for part in self.parts:
            part.render(scale)


class ModelSlime:

    def __init__(self, inner: bool = True):
        if inner:
            self.body = ModelPart(0, 16, -3, 17, -3, 6, 6, 6)
            self.right_eye = ModelPart(32, 0, -3.25, 18, -3.5, 2, 2, 2)
            self.left_eye = ModelPart(32, 4, 1.25, 18, -3.5, 2, 2, 2)
            self.mouth = ModelPart(32, 8, 0, 21, -3.5, 1, 1, 1)
            self.parts = (self.body, self.right_eye, self.left_eye, self.mouth)
        else:
            self.body = ModelPart(0, 0, -4, 16, -4, 8, 8, 8)
            self.parts = (self.body,)

    def render(
        self,
        limb_swing: float,
        limb_amount: float,
        age_in_ticks: float,
        net_head_yaw: float,
        head_pitch: float,
        scale: float = _UNIT,
    ) -> None:
        for part in self.parts:
            part.render(scale)


class ModelSkeleton(ModelBiped):
    def __init__(self):
        super().__init__(inflate=0.0)
        self.biped_right_arm = ModelPart(40, 16, -1, -2, -1, 2, 12, 2)
        self.biped_right_arm.set_rotation_point(-5.0, 2.0, 0.0)
        self.biped_left_arm = ModelPart(40, 16, -1, -2, -1, 2, 12, 2, mirror=True)
        self.biped_left_arm.set_rotation_point(5.0, 2.0, 0.0)
        self.biped_right_leg = ModelPart(0, 16, -1, 0, -1, 2, 12, 2)
        self.biped_right_leg.set_rotation_point(-2.0, 12.0, 0.0)
        self.biped_left_leg = ModelPart(0, 16, -1, 0, -1, 2, 12, 2, mirror=True)
        self.biped_left_leg.set_rotation_point(2.0, 12.0, 0.0)
        self.parts = (
            self.biped_head,
            self.biped_headwear,
            self.biped_body,
            self.biped_right_arm,
            self.biped_left_arm,
            self.biped_right_leg,
            self.biped_left_leg,
        )



class ModelSquid:

    def __init__(self):
        self.body = ModelPart(
            0, 0, -6, -8, -6, 12, 16, 12,
            texture_width=64, texture_height=32,
        )
        self.body.set_rotation_point(0.0, 8.0, 0.0)
        tentacles = []
        for index in range(8):
            angle = index * math.tau / 8.0
            tentacle = ModelPart(
                48, 0, -1, 0, -1, 2, 18, 2,
                texture_width=64, texture_height=32,
            )
            tentacle.set_rotation_point(
                math.cos(angle) * 5.0, 15.0, math.sin(angle) * 5.0,
            )
            tentacle.rotate_angle_y = (
                index * -math.tau / 8.0 + math.pi * 0.5
            )
            tentacles.append(tentacle)
        self.tentacles = tuple(tentacles)
        self.parts = (self.body,) + self.tentacles

    def set_rotation_angles(
        self, limb_swing, limb_amount, tentacle_angle, net_head_yaw, head_pitch,
    ):
        for tentacle in self.tentacles:
            tentacle.rotate_angle_x = float(tentacle_angle)

    def render(
        self, limb_swing, limb_amount, age_in_ticks, net_head_yaw, tentacle_angle,
        scale: float = _UNIT,
    ) -> None:
        self.set_rotation_angles(
            limb_swing, limb_amount, age_in_ticks, net_head_yaw, tentacle_angle,
        )
        for part in self.parts:
            part.render(scale)


class ModelGhast:

    def __init__(self):
        from game.java_random import JavaRandom

        self.body = ModelPart(
            0, 0, -8, -8, -8, 16, 16, 16, texture_width=64, texture_height=32,
        )
        self.body.set_rotation_point(0.0, 8.0, 0.0)
        rng = JavaRandom(1660)
        tentacles = []
        for index in range(9):
            x = (((index % 3) - (index // 3 % 2) * 0.5 + 0.25) / 2.0 * 2.0 - 1.0) * 5.0
            z = ((index // 3) / 2.0 * 2.0 - 1.0) * 5.0
            length = rng.next_int(7) + 8
            tentacle = ModelPart(
                0, 0, -1, 0, -1, 2, length, 2,
                texture_width=64, texture_height=32,
            )
            tentacle.set_rotation_point(x, 15.0, z)
            tentacles.append(tentacle)
        self.tentacles = tuple(tentacles)
        self.parts = (self.body,) + self.tentacles

    def set_rotation_angles(
        self, limb_swing, limb_amount, age_in_ticks, net_head_yaw, head_pitch
    ):
        for index, tentacle in enumerate(self.tentacles):
            tentacle.rotate_angle_x = 0.2 * math.sin(age_in_ticks * 0.3 + index) + 0.4

    def render(
        self, limb_swing, limb_amount, age_in_ticks, net_head_yaw, head_pitch,
        scale: float = _UNIT,
    ) -> None:
        self.set_rotation_angles(
            limb_swing, limb_amount, age_in_ticks, net_head_yaw, head_pitch
        )
        for part in self.parts:
            part.render(scale)

_model_cache: dict[str, object] = {}

def _get_model(kind: str):
    if kind not in _model_cache:
        factories = {
            "pig": ModelPig,
            "pig_saddle": lambda: ModelPig(inflate=0.5),
            "sheep": ModelSheep,
            "sheep_fur": ModelSheepFur,
            "cow": ModelCow,
            "chicken": ModelChicken,
            "creeper": ModelCreeper,
            "spider": ModelSpider,
            "skeleton": ModelSkeleton,
            "slime": lambda: ModelSlime(True),
            "slime_shell": lambda: ModelSlime(False),
            "zombie": ModelBiped,
            "giant": ModelBiped,
            "ghast": ModelGhast,
            "squid": ModelSquid,
        }
        _model_cache[kind] = factories[kind]()
    return _model_cache[kind]


def _wrap_degrees(value: float) -> float:
    return (float(value) + 180.0) % 360.0 - 180.0

def render_mob_model(
    entity,
    world_x: float,
    world_y: float,
    world_z: float,
    body_yaw: float,
    partial: float,
    *,
    model_kind: str,
    spawner_display: bool = False,
    head_yaw: Optional[float] = None,
    head_pitch: Optional[float] = None,
    extra_pass_kind: Optional[str] = None,
    extra_skin_path: Optional[str] = None,
) -> None:
    from game.entities.entity import Entity

    renderer = getattr(entity, "renderer", None) or Entity.renderer
    if renderer is None:
        return

    dx, dy, dz = renderer.camera_relative_offset(world_x, world_y, world_z)

    yaw = float(head_yaw if head_yaw is not None else getattr(entity, "yaw", body_yaw))
    if head_pitch is not None:
        pitch = float(head_pitch)
    else:
        cur = float(getattr(entity, "rotation_pitch", 0.0))
        prev = float(getattr(entity, "prev_rotation_pitch", cur))
        pitch = prev + (cur - prev) * float(partial)
        if hasattr(entity, "pitch") and not hasattr(entity, "rotation_pitch"):
            try:
                pitch = float(entity.pitch)
            except (TypeError, ValueError):
                pass

    limb_swing = (
        float(getattr(entity, "prev_limb_swing", 0.0))
        + (float(getattr(entity, "limb_swing", 0.0)) - float(getattr(entity, "prev_limb_swing", 0.0)))
        * partial
    )
    limb_amount = (
        float(getattr(entity, "prev_limb_swing_amount", 0.0))
        + (
            float(getattr(entity, "limb_swing_amount", 0.0))
            - float(getattr(entity, "prev_limb_swing_amount", 0.0))
        )
        * partial
    )
    age = float(getattr(entity, "ticks_existed", 0)) + partial
    if model_kind == "chicken":
        wing_rotation = getattr(entity, "get_wing_rotation", None)
        if callable(wing_rotation):
            try:
                age = float(wing_rotation(partial))
            except Exception:
                age = 0.0

    net_head = body_yaw - yaw
    while net_head < -180.0:
        net_head += 360.0
    while net_head >= 180.0:
        net_head -= 360.0

    model = _get_model(model_kind)
    squid_tentacle_angle = 0.0
    squid_yaw_animation = 0.0
    if model_kind == "squid":
        squid_tentacle_angle = (
            float(getattr(entity, "prev_tentacle_angle", 0.0))
            + (float(getattr(entity, "tentacle_angle", 0.0))
               - float(getattr(entity, "prev_tentacle_angle", 0.0))) * partial
        )
        squid_yaw_animation = (
            float(getattr(entity, "prev_body_yaw_animation", 0.0))
            + (float(getattr(entity, "body_yaw_animation", 0.0))
               - float(getattr(entity, "prev_body_yaw_animation", 0.0))) * partial
        )
    model_scale = float(getattr(entity, "model_scale", 1.0)) if model_kind == "giant" else 1.0
    ghast_scale = None
    if model_kind == "ghast":
        charge = (
            float(getattr(entity, "prev_charge", 0))
            + (float(getattr(entity, "charge", 0))
               - float(getattr(entity, "prev_charge", 0))) * float(partial)
        ) / 20.0
        charge = max(0.0, charge)
        inverse = 1.0 / (charge ** 5 * 2.0 + 1.0)
        ghast_scale = ((8.0 + 1.0 / inverse) / 2.0,
                       (8.0 + inverse) / 2.0)

    glPushMatrix()
    enable_standard_item_lighting()
    glEnable(GL_NORMALIZE)
    glTranslatef(dx, dy, dz)
    if model_kind == "squid":
        glTranslatef(0.0, 0.5, 0.0)
        glRotatef(180.0 - yaw, 0.0, 1.0, 0.0)
        glRotatef(pitch, 1.0, 0.0, 0.0)
        glRotatef(squid_yaw_animation, 0.0, 1.0, 0.0)
        glTranslatef(0.0, -1.2, 0.0)
    else:
        glRotatef(body_yaw, 0.0, 1.0, 0.0)
    if spawner_display:
        glTranslatef(0.0, 0.4, 0.0)
        glRotatef(SPAWNER_DISPLAY_PITCH, 1.0, 0.0, 0.0)
        glTranslatef(0.0, -0.4, 0.0)
        glScalef(7.0 / 16.0, 7.0 / 16.0, 7.0 / 16.0)

    death_time = float(getattr(entity, "death_time", 0.0))
    if death_time > 0.0 and model_kind != "squid":
        death_progress = max(
            0.0, min(1.0, ((death_time + partial - 1.0) / 20.0 * 1.6) ** 0.5)
        )
        glRotatef(death_progress * 90.0, 0.0, 0.0, 1.0)

    creeper_flash = 0.0
    flash_fn = getattr(entity, "set_creeper_flash_time", None)
    if model_kind == "creeper" and callable(flash_fn):
        try:
            creeper_flash = float(flash_fn(partial))
        except Exception:
            creeper_flash = 0.0
        if creeper_flash < 0.0:
            creeper_flash = 0.0
        if creeper_flash > 1.0:
            creeper_flash = 1.0
        wobble = 1.0 + math.sin(creeper_flash * 100.0) * creeper_flash * 0.01
        t = creeper_flash * creeper_flash
        t = t * t
        scale_xz = (1.0 + t * 0.4) * wobble
        scale_y = (1.0 + t * 0.1) / wobble
        glScalef(scale_xz, scale_y, scale_xz)

    if model_scale != 1.0:
        glScalef(model_scale, model_scale, model_scale)
    if ghast_scale is not None:
        glScalef(ghast_scale[0], ghast_scale[1], ghast_scale[0])

    glScalef(-1.0, -1.0, 1.0)
    if model_kind == "slime":
        slime_size = float(getattr(entity, "slime_size", 1))
        prev_squish = float(getattr(entity, "prev_squish_factor", 0.0))
        squish = float(getattr(entity, "squish_factor", 0.0))
        f3 = (prev_squish + (squish - prev_squish) * partial) / (slime_size * 0.5 + 1.0)
        f4 = 1.0 / (f3 + 1.0)
        glScalef(f4 * slime_size, 1.0 / f4 * slime_size, f4 * slime_size)
    glTranslatef(0.0, -24.0 * _UNIT - 0.0078125, 0.0)

    _bind_skin(entity)
    try:
        brightness = float(entity.get_entity_brightness())
    except Exception:
        brightness = 1.0
    brightness = max(0.0, min(1.0, brightness))
    glColor4f(brightness, brightness, brightness, 1.0)
    glDisable(GL_CULL_FACE)
    glEnable(GL_DEPTH_TEST)

    raised = bool(getattr(entity, "raised_arms", False))
    model.held_item_right = model_kind == "skeleton" and getattr(entity, "_equipped_item", None) is not None
    render_kwargs = {}
    if model_kind in ("skeleton", "zombie", "giant"):
        render_kwargs["raised_arms"] = raised
        render_kwargs["is_riding"] = getattr(entity, "riding_entity", None) is not None
    model.render(
        limb_swing, limb_amount,
        squid_tentacle_angle if model_kind == "squid" else age,
        net_head, -pitch,
        _UNIT, **render_kwargs,
    )

    if model_kind == "skeleton":
        from .player_model import _render_third_person_held_item
        _render_third_person_held_item(entity, model, brightness)

    if model_kind == "creeper" and creeper_flash > 0.0:
        if int(creeper_flash * 10.0) % 2 != 0:
            alpha = max(0.0, min(1.0, creeper_flash * 0.2))
            glDisable(GL_TEXTURE_2D)
            glDisable(GL_ALPHA_TEST)
            glEnable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            glDepthFunc(GL_EQUAL)
            glColor4f(1.0, 1.0, 1.0, alpha)
            model.render(limb_swing, limb_amount, age, net_head, -pitch, _UNIT)
            glDepthFunc(GL_LEQUAL)
            glDisable(GL_BLEND)
            glEnable(GL_ALPHA_TEST)
            glEnable(GL_TEXTURE_2D)
            glColor4f(brightness, brightness, brightness, 1.0)

    if model_kind == "slime":
        shell = _get_model("slime_shell")
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glColor4f(brightness, brightness, brightness, 1.0)
        shell.render(limb_swing, limb_amount, age, net_head, -pitch, _UNIT)
        glDisable(GL_BLEND)
        glEnable(GL_ALPHA_TEST)

    if extra_pass_kind and extra_skin_path:
        saved = getattr(entity, "skin_path", None)
        try:
            entity.skin_path = extra_skin_path
            _bind_skin(entity)
            if model_kind == "sheep" and extra_pass_kind == "sheep_fur":
                from .sheep import Sheep
                color_index = int(getattr(entity, "fleece_color", 0)) & 15
                red, green, blue = Sheep.FUR_COLORS[color_index]
                glColor4f(
                    brightness * red, brightness * green,
                    brightness * blue, 1.0,
                )
            else:
                glColor4f(brightness, brightness, brightness, 1.0)
            extra = _get_model(extra_pass_kind)
            extra.render(limb_swing, limb_amount, age, net_head, -pitch, _UNIT)
        finally:
            entity.skin_path = saved
            _bind_skin(entity)

    if model_kind == "spider":
        saved = getattr(entity, "skin_path", None)
        try:
            entity.skin_path = "mob/spider_eyes.png"
            _bind_skin(entity)
            glEnable(GL_BLEND)
            glDisable(GL_ALPHA_TEST)
            glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
            glColor4f(1.0, 1.0, 1.0, (1.0 - brightness) * 0.5)
            model.render(limb_swing, limb_amount, age, net_head, -pitch, _UNIT)
        finally:
            glDisable(GL_BLEND)
            glEnable(GL_ALPHA_TEST)
            entity.skin_path = saved
            _bind_skin(entity)
            glColor4f(brightness, brightness, brightness, 1.0)

    if float(getattr(entity, "hurt_time", 0.0)) > 0.0 or death_time > 0.0:
        glDisable(GL_TEXTURE_2D)
        glDisable(GL_ALPHA_TEST)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glDepthFunc(GL_EQUAL)
        glColor4f(brightness, 0.0, 0.0, 0.4)
        model.render(
            limb_swing, limb_amount,
            squid_tentacle_angle if model_kind == "squid" else age,
            net_head, -pitch, _UNIT, **render_kwargs,
        )
        if model_kind == "slime":
            _get_model("slime_shell").render(limb_swing, limb_amount, age, net_head, -pitch, _UNIT)
        if extra_pass_kind:
            extra = _get_model(extra_pass_kind)
            extra.render(limb_swing, limb_amount, age, net_head, -pitch, _UNIT)
        glDepthFunc(GL_LEQUAL)
        glDisable(GL_BLEND)
        glEnable(GL_ALPHA_TEST)
        glEnable(GL_TEXTURE_2D)

    glEnable(GL_CULL_FACE)
    glDisable(GL_NORMALIZE)
    disable_standard_item_lighting()
    glColor4f(1.0, 1.0, 1.0, 1.0)
    glPopMatrix()


def draw_living_with_model(entity, model_kind: str, **kwargs) -> None:
    from game.entities.entity import Entity

    if entity.dead or Entity.renderer is None:
        return

    world = Entity.world
    partial = 0.0
    if world is not None:
        try:
            partial = max(0.0, min(1.0, float(world.partial_ticks())))
        except Exception:
            partial = 0.0

    x, y, z = (float(v) for v in entity.pos)
    body_yaw = getattr(entity, "prev_render_yaw_offset", entity.yaw) + _wrap_degrees(
        getattr(entity, "render_yaw_offset", entity.yaw)
        - getattr(entity, "prev_render_yaw_offset", entity.yaw)
    ) * partial
    head_yaw = getattr(entity, "prev_yaw", entity.yaw) + _wrap_degrees(
        entity.yaw - getattr(entity, "prev_yaw", entity.yaw)
    ) * partial

    try:
        from game.entities.entity_shadow import (
            SHADOW_SIZE_LIVING,
            render_entity_shadow,
            shadow_opacity_for_distance,
        )

        opacity = shadow_opacity_for_distance(world, x, y, z)
        if opacity > 0.0:
            shadow_size = float(getattr(entity, "shadow_size", SHADOW_SIZE_LIVING)) * float(
                getattr(entity, "model_scale", 1.0)
            )
            render_entity_shadow(world, x, y, z, shadow_size, opacity=opacity)
    except Exception:
        pass

    render_y = float(getattr(entity, "render_y_offset", 0.0))
    head_pitch = float(getattr(entity, "rotation_pitch", 0.0))
    if model_kind == "squid":
        previous_pitch = float(getattr(entity, "prev_rotation_pitch", head_pitch))
        head_pitch = previous_pitch + (head_pitch - previous_pitch) * partial

    render_mob_model(
        entity,
        x,
        y - render_y,
        z,
        body_yaw,
        partial,
        model_kind=model_kind,
        head_yaw=head_yaw,
        head_pitch=head_pitch,
        **kwargs,
    )

    if int(getattr(entity, "_fire_ticks", 0)) > 0:
        try:
            from game.entities import player_model

            player_model.render_player_fire_effect(entity, x, y - render_y, z)
        except Exception:
            pass
