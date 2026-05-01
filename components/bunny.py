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
    def __init__(self, screen_size: pygame.math.Vector2, name):
        self.GRAVITY = pygame.math.Vector2(0.0, 200.0)
        self.JUMP_VELOCITY = -self.GRAVITY * 1.0
        self.MOVE_VELOCITY = pygame.math.Vector2(50.0, 0.0)
        self.BUNNY_SIZE = pygame.math.Vector2(48.0, 64.0)
        self.SCREEN_SIZE = screen_size

        self.name = name
        # 饱食度，范围 0-100，初始为 50，每 30 秒减少 1 点
        self.satiety = 50
        self.satiety_timer = 30.0

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
        self.current_comment = None
        self.comment_display_timer = 0.0
        if getattr(sys, "frozen", False):
            jump_sound_path = os.path.join(sys._MEIPASS, constants.JUMP_WAV_PATH)
            self.jump_sound = pygame.mixer.Sound(jump_sound_path)
        else:
            self.jump_sound = pygame.mixer.Sound(constants.JUMP_WAV_PATH)

        self.platforms = [Platform(pygame.math.Vector2(0, screen_size.y), pygame.math.Vector2(screen_size.x, 1000.0))]

    def set_platforms(self, platforms: List[Platform]):
        self.platforms = platforms

    def set_comment(self, comment: str):
        """设置要显示的评论，显示 6 秒"""
        self.current_comment = comment
        self.comment_display_timer = 6.0

    def _draw_speech_bubble(self, screen: pygame.Surface):
        if not self.current_comment:
            return
        
        # ✅ 使用支持中文的字体
        font_paths = [
            "C:/Windows/Fonts/msyh.ttc",        # 微软雅黑
            "C:/Windows/Fonts/simhei.ttf",       # 黑体
            "C:/Windows/Fonts/simsun.ttc",       # 宋体
            "C:/Windows/Fonts/msyhbd.ttc",       # 微软雅黑粗体
        ]
        
        font = None
        for path in font_paths:
            if os.path.exists(path):
                font = pygame.font.Font(path, 22)
                break
        
        if font is None:
            # 兜底：用系统默认字体（可能不支持中文）
            font = pygame.font.Font(None, 22)
        
        # 气泡位置（兔子头上方）
        bunny_top = self.current_position.y - self.BUNNY_SIZE.y / 2
        bubble_x = self.current_position.x
        bubble_y = bunny_top - 50
        
        # 文字渲染（白色）
        text_surface = font.render(self.current_comment, True, (0, 0, 0))
        text_rect = text_surface.get_rect(center=(bubble_x, bubble_y))
        
        # 气泡背景
        padding_x, padding_y = 12, 8
        bg_rect = text_rect.inflate(padding_x * 2, padding_y * 2)
        
        # 确保气泡不超出屏幕
        screen_width = screen.get_width()
        if bg_rect.left < 10:
            bg_rect.left = 10
            text_rect.centerx = bg_rect.centerx
        if bg_rect.right > screen_width - 10:
            bg_rect.right = screen_width - 10
            text_rect.centerx = bg_rect.centerx
        
        # 画气泡背景
        pygame.draw.rect(screen, (253, 247, 249), bg_rect, border_radius=10)
        pygame.draw.rect(screen, (180, 180, 180), bg_rect, 2, border_radius=10)
        
        # 气泡三角
        arrow_x = self.current_position.x
        points = [
            (arrow_x - 8, bg_rect.bottom),
            (arrow_x, bg_rect.bottom + 10),
            (arrow_x + 8, bg_rect.bottom),
        ]
        pygame.draw.polygon(screen, (253, 247, 249), points)
        pygame.draw.polygon(screen, (180, 180, 180), points, 1)
        
        # 画文字
        screen.blit(text_surface, text_rect)

    def startup(self):
        self.current_state = BunnyState.IDLE
        self.enter_idle()
        
    def update(self, delta: float):
        # 更新评论显示计时器
        if self.current_comment:
            self.comment_display_timer -= delta
            if self.comment_display_timer <= 0:
                self.current_comment = None

        self.satiety_timer -= delta
        if self.satiety_timer <= 0:
            self.satiety_timer = 30.0
            self.satiety = max(0, self.satiety - 1)

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

        # ✅ 画对话气泡
        self._draw_speech_bubble(screen)

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
        if self.huge_jump_flag and len(platforms_can_jump) > 0:
            self.huge_jump_flag = False

            huge_jump_platform_index = random.randint(0, len(platforms_can_jump) - 1)
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
    
    def handle_click(self, mouse_pos: tuple) -> bool:
        if self.is_position_inside_bunny(mouse_pos):
            self.on_clicked()
            return True
        return False
    
    def is_position_inside_bunny(self, pos: tuple) -> bool:
        image = self.sprite.get_draw_image()
        draw_x = self.current_position.x - image.get_width() / 2
        draw_y = self.current_position.y - image.get_height() / 2
        
        bunny_rect = pygame.Rect(draw_x, draw_y, image.get_width(), image.get_height())
        return bunny_rect.collidepoint(pos)

    def on_clicked(self):
        if self.current_state == BunnyState.IDLE or self.current_state == BunnyState.SPECIAL:
            self.idle_timer = 0.0
            self.special_timer = 0.0
            self.jump_cnt = random.randint(3, 5)
            self.huge_jump_flag = (random.randint(0, 100) < 80)
            self.change_state(BunnyState.JUMP)
            return

    def eat_carrot(self, carrot_path=None):
        if carrot_path and os.path.isfile(carrot_path):
            try:
                carrot_name = os.path.basename(carrot_path)
                if "carrot" in carrot_name.lower():
                    os.remove(carrot_path)
                    self.satiety = min(self.satiety + 10, 100)
                    self.set_comment("嗷呜~好甜的萝卜呀~")
                    print(f"carrot eaten: {carrot_path}, current satiety: {self.satiety}")
                else:
                    self.set_comment("我不吃萝卜以外的东西~")
                    print(f"File is not a carrot: {carrot_path}")
            except Exception as e:
                print(f"Failed to delete file: {e}")
