import os
import weakref

import pygame as pg
from OpenGL.GL import *

from engine.anaglyph import anaglyph_surface
from game.app.texturepacks import resolve_texture_path


class Texture:
    _cache = {}  
    _instances = weakref.WeakSet()
    _anaglyph_enabled = False
    
    def __init__(
        self,
        image_path,
        repeat_mode=GL_REPEAT,
        x=0,
        y=0,
        u_size=1,
        v_size=1,
        min_filter=GL_NEAREST,
        mag_filter=GL_NEAREST,
    ):
        self.repeat_mode = repeat_mode
        self.min_filter = min_filter
        self.mag_filter = mag_filter
        self.texture_id = None
        self.width = 0
        self.height = 0
        self.x = x
        self.y = y
        self.u_size = u_size
        self.v_size = v_size
        
        self._source_path = None
        self._surface_source = None
        if isinstance(image_path, (str, os.PathLike)):
            self._source_path = str(image_path)
            self._load_from_file(resolve_texture_path(image_path))
        else:
            self._surface_source = image_path.copy()
            self._load_from_surface(image_path)
        Texture._instances.add(self)
    
    @classmethod
    def _cache_key(cls, filepath):
        return os.path.abspath(filepath)
    
    @classmethod
    def refresh_all_for_active_pack(cls):
        instances = list(cls._instances)
        old_ids = {
            int(texture.texture_id)
            for texture in instances
            if getattr(texture, "_source_path", None) and texture.texture_id
        }
        old_ids.update(
            int(item["texture_id"])
            for item in cls._cache.values()
            if item.get("texture_id")
        )
        cls._cache.clear()

        for texture in instances:
            source_path = getattr(texture, "_source_path", None)
            if not source_path:
                continue
            texture.texture_id = None
            texture.width = 0
            texture.height = 0
            texture._load_from_file(resolve_texture_path(source_path))

        if old_ids:
            glDeleteTextures(list(old_ids))
        return len(old_ids)

    @classmethod
    def set_anaglyph_enabled(cls, enabled):
        enabled = bool(enabled)
        if enabled == cls._anaglyph_enabled:
            return 0
        cls._anaglyph_enabled = enabled

        instances = list(cls._instances)
        old_ids = {
            int(texture.texture_id)
            for texture in instances
            if texture.texture_id
        }
        old_ids.update(
            int(item["texture_id"])
            for item in cls._cache.values()
            if item.get("texture_id")
        )
        cls._cache.clear()

        for texture in instances:
            texture.texture_id = None
            texture.width = 0
            texture.height = 0
            source_path = texture._source_path
            if source_path:
                texture._load_from_file(resolve_texture_path(source_path))
            elif texture._surface_source is not None:
                texture._load_from_surface(texture._surface_source)

        if old_ids:
            glDeleteTextures(list(old_ids))
        return len(old_ids)

    def _load_from_file(self, filepath):
        cache_key = (
            self._cache_key(filepath),
            int(self.repeat_mode),
            int(self.min_filter),
            int(self.mag_filter),
        )
        
        if cache_key in Texture._cache:
            cached = Texture._cache[cache_key]
            self.texture_id = cached['texture_id']
            self.width = cached['width']
            self.height = cached['height']
            return
        
        try:
            if not os.path.exists(filepath):
                print(f"Warning: Texture file not found: {filepath}")
                self._create_default_texture()
                return
            
            surface = pg.image.load(filepath)
            self._load_from_surface(surface)
            
            Texture._cache[cache_key] = {
                'texture_id': self.texture_id,
                'width': self.width,
                'height': self.height
            }
        except Exception as e:
            print(f"Error loading texture {filepath}: {e}")
            self._create_default_texture()
    
    def _load_from_surface(self, surface):
        surface_rgba = (
            anaglyph_surface(surface)
            if Texture._anaglyph_enabled else surface.convert_alpha()
        )
        
        self.width, self.height = surface_rgba.get_size()
        texture_data = pg.image.tostring(surface_rgba, "RGBA", True)
        
        self.texture_id = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, self.texture_id)
        
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, self.repeat_mode)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, self.repeat_mode)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, self.mag_filter)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, self.min_filter)
        
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, self.width, self.height, 0,
                    GL_RGBA, GL_UNSIGNED_BYTE, texture_data)
        
        glBindTexture(GL_TEXTURE_2D, 0)
    
    def _create_default_texture(self):
        default_surface = pg.Surface((64, 64))
        default_surface.fill((255, 255, 255))
        self._load_from_surface(default_surface)
    
    def bind(self, *, alpha_test: bool = True, alpha_ref: float = 0.5):
        if self.texture_id:
            glBindTexture(GL_TEXTURE_2D, self.texture_id)
            if alpha_test:
                glEnable(GL_ALPHA_TEST)
                glAlphaFunc(GL_GREATER, float(alpha_ref))
            else:
                glDisable(GL_ALPHA_TEST)
    
    def unbind(self):
        glBindTexture(GL_TEXTURE_2D, 0)
        glDisable(GL_ALPHA_TEST)

    def update_tile_rgba(self, tile_index, rgba_bytes, tile_size=16, tile_span=1, tile_row_step=1):
        if not self.texture_id or not rgba_bytes:
            return
        tile_size = int(tile_size)
        tile_span = max(1, int(tile_span))
        if len(rgba_bytes) != tile_size * tile_size * 4:
            raise ValueError("RGBA tile byte count does not match tile_size")
        cols = max(1, self.width // tile_size)
        rows = max(1, self.height // tile_size)
        start_col = int(tile_index) % cols
        start_row = int(tile_index) // cols
        tile_row_step = int(tile_row_step)
        target_rows = [start_row + dy * tile_row_step for dy in range(tile_span)]
        if start_col + tile_span > cols or any(row < 0 or row >= rows for row in target_rows):
            raise ValueError("Repeated texture tile region exceeds the atlas")

        src = memoryview(rgba_bytes)
        flipped = bytearray(tile_size * tile_size * 4)
        row_bytes = tile_size * 4
        for row_idx in range(tile_size):
            src_off = row_idx * row_bytes
            dst_off = (tile_size - 1 - row_idx) * row_bytes
            flipped[dst_off:dst_off + row_bytes] = src[src_off:src_off + row_bytes]
        flipped_bytes = bytes(flipped)
        glBindTexture(GL_TEXTURE_2D, self.texture_id)
        try:
            if tile_span > 1 and tile_row_step == 1:
                upload_size = tile_size * tile_span
                upload_row_bytes = upload_size * 4
                repeated = bytearray(upload_size * upload_size * 4)
                for tile_y in range(tile_span):
                    tile_offset = tile_y * tile_size * upload_row_bytes
                    for row_idx in range(tile_size):
                        src_off = row_idx * row_bytes
                        dst_off = tile_offset + row_idx * upload_row_bytes
                        row = flipped_bytes[src_off:src_off + row_bytes] * tile_span
                        repeated[dst_off:dst_off + upload_row_bytes] = row
                y = self.height - (start_row + tile_span) * tile_size
                glTexSubImage2D(
                    GL_TEXTURE_2D, 0, start_col * tile_size, y,
                    upload_size, upload_size, GL_RGBA, GL_UNSIGNED_BYTE,
                    bytes(repeated),
                )
                return

            for dy, image_row in enumerate(target_rows):
                for dx in range(tile_span):
                    x = (start_col + dx) * tile_size
                    y = self.height - (image_row + 1) * tile_size
                    glTexSubImage2D(
                        GL_TEXTURE_2D, 0, x, y, tile_size, tile_size,
                        GL_RGBA, GL_UNSIGNED_BYTE, flipped_bytes,
                    )
        finally:
            glBindTexture(GL_TEXTURE_2D, 0)

    def delete(self):
        if self.texture_id:
            glDeleteTextures([self.texture_id])
            self.texture_id = None