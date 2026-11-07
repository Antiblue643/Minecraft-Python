
from game.world.biomes import VoidBiomeProvider


class DebugTerrainGenerator:
    PLATFORM_RADIUS = 16
    PLATFORM_Y = 64

    def __init__(self, seed):
        self.seed = int(seed)
        self.biomes = VoidBiomeProvider(self.seed)
        self.snow_covered = False

    def generate_chunk_data(self, chunk_x, chunk_z, width, depth, block_ids):
        height = 128
        blocks = bytearray(int(width) * int(depth) * height)
        stone_id = 3
        start_x = int(chunk_x) * int(width)
        start_z = int(chunk_z) * int(depth)
        for local_x in range(int(width)):
            world_x = start_x + local_x
            if abs(world_x) > self.PLATFORM_RADIUS:
                continue
            for local_z in range(int(depth)):
                world_z = start_z + local_z
                if abs(world_z) <= self.PLATFORM_RADIUS:
                    index = (local_x * int(depth) + local_z) * height + self.PLATFORM_Y
                    blocks[index] = stone_id
        return blocks

    def height_at(self, x, z):
        if (abs(int(x)) <= self.PLATFORM_RADIUS
                and abs(int(z)) <= self.PLATFORM_RADIUS):
            return self.PLATFORM_Y
        return 0

    def chunk_surface_range(self, chunk_x, chunk_z):
        start_x, start_z = int(chunk_x) * 16, int(chunk_z) * 16
        end_x, end_z = start_x + 15, start_z + 15
        if (start_x <= self.PLATFORM_RADIUS and end_x >= -self.PLATFORM_RADIUS
                and start_z <= self.PLATFORM_RADIUS and end_z >= -self.PLATFORM_RADIUS):
            return self.PLATFORM_Y, self.PLATFORM_Y
        return 0, 0

    @staticmethod
    def populate_chunk(_world, _chunk_x, _chunk_z):
        return None

    @staticmethod
    def _remember_surface(*_args):
        return None
