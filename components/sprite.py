import pygame
import os
import sys

class Sprite:
    def __init__(self, sprite_path: str, total_frames):
        self.FIXED_SPRITE_SIZE = 32

        if getattr(sys, "frozen", False):
            sprite_path = os.path.join(sys._MEIPASS, sprite_path)
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