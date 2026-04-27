from sprite import Sprite
from constants import constants

class AnimationPlayer:
    def __init__(self):
        self.IDLE_SPRITE = Sprite(constants.BUNNY_IDLE_PNG, 8)
        self.JUMP_SPRITE = Sprite(constants.BUNNY_JUMP_PNG, 4)
        self.FLOATING_SPRITE = Sprite(constants.BUNNY_FLOATING_PNG, 4)
        self.FALLING_SPRITE = Sprite(constants.BUNNY_FALLING_PNG, 4)
        self.SPECIAL_SPRITE = Sprite(constants.BUNNY_SPECIAL_PNG, 4)

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
