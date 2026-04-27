from enum import Enum
import pygame.math
import random

class Sprite:
    def __init__(self, sprite_path: str, total_frames):
        self.FIXED_SPRITE_SIZE = 32

        image = pygame.image.load(sprite_path).convert_alpha()

        self.total_frames = total_frames
        self.current_frame = 0
        self.flip_h = False
        self.frame_sprites = []
        for i in range(self.total_frames):
            self.frame_sprites.append(
                pygame.transform.scale(
                    image.subsurface(
                        pygame.Rect(
                            i * self.FIXED_SPRITE_SIZE,
                            0,
                            self.FIXED_SPRITE_SIZE,
                            self.FIXED_SPRITE_SIZE
                        )),
                    (128, 128)
                )
            )
        self.flipped_frame_sprites = [pygame.transform.flip(frame, True, False) for frame in self.frame_sprites]
    
    def get_draw_image(self):
        if self.flip_h:
            return self.flipped_frame_sprites[self.current_frame]
        return self.frame_sprites[self.current_frame]

class AnimationPlayer:
    def __init__(self):
        self.IDLE_SPRITE = Sprite("assets/BunnyIdle.png", 8)
        self.JUMP_SPRITE = Sprite("assets/BunnyJump.png", 4)
        self.FLOATING_SPRITE = Sprite("assets/BunnyFloating.png", 4)
        self.FALLING_SPRITE = Sprite("assets/BunnyFalling.png", 4)
        self.SPECIAL_SPRITE = Sprite("assets/BunnySpecial.png", 4)

        self.fps = 6
        self.current_anim = "Idle"
        self.current_playing_sprite = None
        self.next_frame_timer = 0.0

    def play(self, sprite: Sprite, anim_name: str):
        self.current_anim = anim_name
        self.current_playing_sprite = sprite
        match self.current_anim:
            case "Idle":
                self.current_playing_sprite.frame_sprites = self.IDLE_SPRITE.frame_sprites
                self.current_playing_sprite.flipped_frame_sprites = self.IDLE_SPRITE.flipped_frame_sprites
                self.current_playing_sprite.total_frames = self.IDLE_SPRITE.total_frames
            case "Jump":
                self.current_playing_sprite.frame_sprites = self.JUMP_SPRITE.frame_sprites
                self.current_playing_sprite.flipped_frame_sprites = self.JUMP_SPRITE.flipped_frame_sprites
                self.current_playing_sprite.total_frames = self.JUMP_SPRITE.total_frames
            case "Floating":
                self.current_playing_sprite.frame_sprites = self.FLOATING_SPRITE.frame_sprites
                self.current_playing_sprite.flipped_frame_sprites = self.FLOATING_SPRITE.flipped_frame_sprites
                self.current_playing_sprite.total_frames = self.FLOATING_SPRITE.total_frames
            case "Falling":
                self.current_playing_sprite.frame_sprites = self.FALLING_SPRITE.frame_sprites
                self.current_playing_sprite.flipped_frame_sprites = self.FALLING_SPRITE.flipped_frame_sprites
                self.current_playing_sprite.total_frames = self.FALLING_SPRITE.total_frames
            case "Special":
                self.current_playing_sprite.frame_sprites = self.SPECIAL_SPRITE.frame_sprites
                self.current_playing_sprite.flipped_frame_sprites = self.SPECIAL_SPRITE.flipped_frame_sprites
                self.current_playing_sprite.total_frames = self.SPECIAL_SPRITE.total_frames
        self.current_playing_sprite.current_frame = 0
        self.next_frame_timer = 0.0
    
    def update(self, delta: float):
        if self.current_playing_sprite is None:
            return
        
        self.next_frame_timer -= delta
        if self.next_frame_timer <= 0.0:
            self.current_playing_sprite.current_frame = (self.current_playing_sprite.current_frame + 1) % self.current_playing_sprite.total_frames
            self.next_frame_timer += 1.0 / self.fps

class Platform:
    def __init__(self, new_vertex, new_size):
        self.vertex = new_vertex
        self.size = new_size
    
    def get_left_x(self):
        return self.vertex.x
    
    def get_right_x(self):
        return self.vertex.x + self.size.x

    def get_bottom_y(self):
        return self.vertex.y + self.size.y

    def get_top_y(self):
        return self.vertex.y

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

        self.sprite = Sprite("assets/BunnyIdle.png", 8)
        self.current_state = BunnyState.IDLE
        self.current_position = pygame.math.Vector2(
            random.randint(int(screen_size.x / 4), int(screen_size.x * 3 / 4)),
            screen_size.y / 2.0
        )
        self.current_velocity = pygame.math.Vector2(0.0, 0.0)
        self.current_direction = random.randint(0, 1) * 2 - 1
        self.sprite.flip_h = (self.current_direction == 1)
        self.idle_timer = 0.0
        self.special_timer = 0.0
        self.jump_cnt = 0
        self.anim_player = AnimationPlayer()
        self.jump_sound = pygame.mixer.Sound("assets/se_jump.wav") 

        self.platforms = [Platform(pygame.math.Vector2(0, screen_size.y), pygame.math.Vector2(screen_size.x, 1000.0))]

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
