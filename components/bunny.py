from enum import Enum
from components.animation_player import AnimationPlayer
from components.bunny_platform import Platform
import pygame.math
import math
import os
import random
from components.sprite import Sprite
import sys
from constants import constants
from typing import List


class BunnyState(Enum):
    IDLE = 0
    JUMP = 1
    FLOATING = 2
    FALLING = 3
    SPECIAL = 4

class Bunny:
    def __init__(self, screen_size: pygame.math.Vector2):
        self.GRAVITY = pygame.math.Vector2(0.0, 200.0)
        self.JUMP_VELOCITY = -self.GRAVITY * 1.0
        self.MOVE_VELOCITY = pygame.math.Vector2(50.0, 0.0)
        self.BUNNY_SIZE = pygame.math.Vector2(48.0, 64.0)
        self.SCREEN_SIZE = screen_size

        self.sprite = Sprite(constants.BUNNY_IDLE_PNG, 8)
        self.current_state = BunnyState.IDLE
        self.current_position = pygame.math.Vector2(
            random.randint(int(screen_size.x / 4), int(screen_size.x * 3 / 4)),
            0.0
        )
        self.current_velocity = pygame.math.Vector2(0.0, 0.0)
        self.current_direction = random.randint(0, 1) * 2 - 1
        self.sprite.flip_h = (self.current_direction == 1)
        self.idle_timer = 0.0
        self.special_timer = 0.0
        self.jump_cnt = 0
        self.huge_jump_flag = False
        self.anim_player = AnimationPlayer()
        if getattr(sys, "frozen", False):
            jump_sound_path = os.path.join(sys._MEIPASS, constants.JUMP_WAV_PATH)
            self.jump_sound = pygame.mixer.Sound(jump_sound_path)
        else:
            self.jump_sound = pygame.mixer.Sound(constants.JUMP_WAV_PATH)

        self.platforms = [Platform(pygame.math.Vector2(0, screen_size.y), pygame.math.Vector2(screen_size.x, 1000.0))]

    def set_platforms(self, platforms: List[Platform]):
        self.platforms = platforms

    def startup(self):
        self.current_state = BunnyState.IDLE
        self.enter_idle()
        
    def update(self, delta: float):
        self.anim_player.update(delta)
        match self.current_state:
            case BunnyState.IDLE:
                self.update_idle(delta)
            case BunnyState.JUMP:
                self.update_jump(delta)
            case BunnyState.FLOATING:
                self.update_floating(delta)
            case BunnyState.FALLING:
                self.update_falling(delta)
            case BunnyState.SPECIAL:
                self.update_special(delta)

    def draw(self, delta: float, screen: pygame.Surface):
        image = self.sprite.get_draw_image()
        draw_x = self.current_position.x - image.get_width() / 2
        draw_y = self.current_position.y - image.get_height() / 2
        screen.blit(image, (draw_x, draw_y))

    def update_idle(self, delta: float):
        if not self.is_on_floor_and_adjust_y():
            self.change_state(BunnyState.FALLING)
            return
        
        if self.jump_cnt > 0:
            self.change_state(BunnyState.JUMP)
            return
        
        self.idle_timer -= delta
        if self.idle_timer <= 0.0:
            if random.randint(0, 1) == 0:
                self.jump_cnt = random.randint(3, 5)
                self.huge_jump_flag = (random.randint(0, 100) < 80)
                self.change_state(BunnyState.JUMP)
                return
            else:
                self.change_state(BunnyState.SPECIAL)
                return

    def update_jump(self, delta: float):
        if self.sprite.current_frame >= 3:
            self.change_state(BunnyState.FLOATING)

    def update_floating(self, delta: float):
        self.current_velocity += self.GRAVITY * delta
        self.current_position += self.current_velocity * delta
        if self.current_direction == -1 and self.get_bunny_left_x() <= 0:
            self.current_direction = -self.current_direction
            self.current_velocity.x = -self.current_velocity.x
            self.sprite.flip_h = (self.current_direction == 1)
        if self.current_direction == 1 and self.get_bunny_right_x() >= self.SCREEN_SIZE.x:
            self.current_direction = -self.current_direction
            self.current_velocity.x = -self.current_velocity.x
            self.sprite.flip_h = (self.current_direction == 1)
        if self.current_velocity.y >= 0:
            self.change_state(BunnyState.FALLING)

    def update_falling(self, delta: float):
        self.current_velocity += self.GRAVITY * delta
        self.current_position += self.current_velocity * delta
        if self.current_direction == -1 and self.get_bunny_left_x() <= 0:
            self.current_direction = -self.current_direction
            self.current_velocity.x = -self.current_velocity.x
            self.sprite.flip_h = (self.current_direction == 1)
        if self.current_direction == 1 and self.get_bunny_right_x() >= self.SCREEN_SIZE.x:
            self.current_direction = -self.current_direction
            self.current_velocity.x = -self.current_velocity.x
            self.sprite.flip_h = (self.current_direction == 1)
        if self.is_on_floor_and_adjust_y():
            self.change_state(BunnyState.IDLE)

    def update_special(self, delta: float):
        self.special_timer -= delta
        if self.special_timer <= 0.0:
            self.change_state(BunnyState.IDLE)
            return

    def enter_idle(self):
        self.anim_player.play(self.sprite, "Idle")
        
        self.current_velocity = pygame.math.Vector2(0.0, 0.0)
        self.idle_timer = random.uniform(5.0, 15.0)

    def enter_jump(self):
        self.anim_player.play(self.sprite, "Jump")
        self.jump_cnt -= 1
        self.jump_sound.play()

    def enter_floating(self):
        self.anim_player.play(self.sprite, "Floating")
        platforms_can_jump = [platform for platform in self.platforms if platform.get_top_y() < self.current_position.y]
        if self.huge_jump_flag and len(platforms_can_jump) > 1:
            self.huge_jump_flag = False

            huge_jump_platform_index = random.randint(1, len(platforms_can_jump) - 1)
            target_platform = platforms_can_jump[huge_jump_platform_index]

            landing_x = random.randint(int(target_platform.get_left_x()), int(target_platform.get_right_x()))
            landing_y = target_platform.get_top_y()
            
            self.current_direction = 1 if landing_x > self.current_position.x else -1
            self.sprite.flip_h = (self.current_direction == 1)

            abs_x = abs(landing_x - self.current_position.x)
            abs_y = abs(landing_y - self.current_position.y)

            huge_jump_time = math.sqrt((2 * abs_y + 200) / self.GRAVITY.y) + math.sqrt(200.0 / self.GRAVITY.y)
            self.current_velocity = pygame.math.Vector2(0.0, -math.sqrt((2 * abs_y + 200) * self.GRAVITY.y)) + pygame.math.Vector2(abs_x / huge_jump_time, 0.0) * self.current_direction
        else:
            self.current_velocity = self.JUMP_VELOCITY + self.MOVE_VELOCITY * self.current_direction

    def enter_falling(self):
        self.anim_player.play(self.sprite, "Falling")

    def enter_special(self):
        self.anim_player.play(self.sprite, "Special")
        self.special_timer = random.uniform(3.0, 5.0)

    def exit_idle(self):
        pass

    def exit_jump(self):
        pass

    def exit_floating(self):
        pass

    def exit_falling(self):
        pass

    def exit_special(self):
        pass

    def change_state(self, new_state: BunnyState):
        match self.current_state:
            case BunnyState.IDLE:
                self.exit_idle()
            case BunnyState.JUMP:
                self.exit_jump()
            case BunnyState.FLOATING:
                self.exit_floating()
            case BunnyState.FALLING:
                self.exit_falling()
            case BunnyState.SPECIAL:
                self.exit_special()
        self.current_state = new_state
        match self.current_state:
            case BunnyState.IDLE:
                self.enter_idle()
            case BunnyState.JUMP:
                self.enter_jump()
            case BunnyState.FLOATING:
                self.enter_floating()
            case BunnyState.FALLING:
                self.enter_falling()
            case BunnyState.SPECIAL:
                self.enter_special()

    def get_bunny_bottom_y(self) -> float:
        return self.current_position.y + self.BUNNY_SIZE.y / 2.0

    def get_bunny_left_x(self) -> float:
        return self.current_position.x - self.BUNNY_SIZE.x / 2.0

    def get_bunny_right_x(self) -> float:
        return self.current_position.x + self.BUNNY_SIZE.x / 2.0

    def is_on_floor_and_adjust_y(self) -> bool:
        for platform in self.platforms:
            if self.get_bunny_bottom_y() <= platform.get_bottom_y() \
            and self.get_bunny_bottom_y() >= platform.get_top_y() \
            and self.get_bunny_left_x() <= platform.get_right_x() \
            and self.get_bunny_right_x() >= platform.get_left_x():
                self.current_position.y = platform.get_top_y() - self.BUNNY_SIZE.y / 2.0 + 1.0
                return True
        return False
