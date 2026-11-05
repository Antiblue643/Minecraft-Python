import math, random

class Helpers:
    
    @staticmethod
    def gen_uuid():
        a = []
        a.append("uuid_")
        for i in range(1, 8):
            if i % 2 == 0:
                a.append("_")
            else:
                a.append("{:04d}".format(random.randint(0, 9999)))
        return "".join(a)
    
    @staticmethod
    def vec_to_euler(vec):
        x, y, z = vec
        yaw = math.degrees(math.atan2(x, z))  
        pitch = math.degrees(math.atan2(-y, math.hypot(x, z)))
        return pitch, yaw, 0.0

    @staticmethod
    def get_rot_points(point1, point2):
        x1, y1, z1 = point1
        x2, y2, z2 = point2

        dx = x2 - x1
        dy = y2 - y1
        dz = z2 - z1

        magnitude = math.sqrt(dx**2 + dy**2 + dz**2)

        if magnitude == 0:
            return 0, 0, 0
        else:
            dx_norm = dx / magnitude
            dy_norm = dy / magnitude
            dz_norm = dz / magnitude
            return dx_norm, dy_norm, dz_norm
        
    @staticmethod
    def pos_to_atlas_idx(tilesize, x, y):
        unit = 1 / (tilesize // 2)
        return x * unit, 1 - (y + 1) * unit, unit

    @staticmethod
    def idx_to_atlas_pos(tilesize, atlasize, idx):
        x = (idx % atlasize) * (tilesize / atlasize)
        y = (idx // atlasize) * (tilesize / atlasize)
        return x, y