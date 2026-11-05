from __future__ import annotations

from game.content_ids import legacy_entity_id

_SPAWNER_MODELS = {
    legacy_entity_id("creeper"): ("game.entities.creeper", "Creeper", "creeper"),
    legacy_entity_id("pig"): ("game.entities.pig", "Pig", "pig"),
    legacy_entity_id("sheep"): ("game.entities.sheep", "Sheep", "sheep"),
    legacy_entity_id("cow"): ("game.entities.cow", "Cow", "cow"),
    legacy_entity_id("chicken"): ("game.entities.chicken", "Chicken", "chicken"),
    legacy_entity_id("skeleton"): ("game.entities.skeleton", "Skeleton", "skeleton"),
    legacy_entity_id("spider"): ("game.entities.spider", "Spider", "spider"),
    legacy_entity_id("zombie"): ("game.entities.zombie", "Zombie", "zombie"),
    legacy_entity_id("zombie_pigman"): ("game.entities.pigzombie", "ZombiePigman", "zombie"),
    legacy_entity_id("giant"): ("game.entities.giant", "Giant", "giant"),
    legacy_entity_id("slime"): ("game.entities.slime", "Slime", "slime"),
    legacy_entity_id("ghast"): ("game.entities.ghast", "Ghast", "ghast"),
}


def _display_entity(te):
    model = _SPAWNER_MODELS.get(str(getattr(te, "mob_id", "")))
    if model is None:
        return None
    if getattr(te, "_display_mob_id", None) != te.mob_id:
        module_name, class_name, _model_kind = model
        module = __import__(module_name, fromlist=[class_name])
        entity_type = getattr(module, class_name)
        te._display_entity = entity_type(
            (float(te.x), float(te.y) - 0.5, float(te.z)),
            yaw=0.0,
        )
        te._display_mob_id = te.mob_id
    return te._display_entity, model[2]


def draw_all_mob_spawners(world, max_dist=64.0):
    renderer = getattr(world, "renderer", None)
    if renderer is None:
        return
    camera_x = float(getattr(renderer, "camX", 0.0))
    camera_y = float(getattr(renderer, "camY", 0.0))
    camera_z = float(getattr(renderer, "camZ", 0.0))
    max_dist_sq = float(max_dist) ** 2
    try:
        frustum = world._camera_frustum()
    except Exception:
        frustum = None

    from game.entities.mob_models import render_mob_model
    for te in list(getattr(world, "tile_entities", {}).values()):
        if not hasattr(te, "mob_id"):
            continue
        try:
            chunk_key = world._get_chunk_index(te.x, te.z)
            if (chunk_key not in world.chunks
                    or world.get_block_type(te.x, te.y, te.z) != "mob_spawner"):
                continue
            in_active_view = getattr(world, "_chunk_in_active_view", None)
            if callable(in_active_view) and not in_active_view(*chunk_key):
                continue
            dx, dy, dz = te.x - camera_x, te.y - camera_y, te.z - camera_z
            if dx * dx + dy * dy + dz * dz > max_dist_sq:
                continue
            if (frustum is not None and not frustum.intersects_aabb(
                    te.x - 0.5, te.y - 0.5, te.z - 0.5,
                    te.x + 0.5, te.y + 0.5, te.z + 0.5, padding=0.25)):
                continue
            display = _display_entity(te)
            if display is None:
                continue
            entity, model_kind = display
            partial = float(world.partial_ticks())
            yaw = float(te.prev_yaw) + (float(te.yaw) - float(te.prev_yaw)) * partial
            render_mob_model(
                entity,
                float(te.x),
                float(te.y) - 0.5,
                float(te.z),
                yaw * 10.0,
                partial,
                model_kind=model_kind,
                spawner_display=True,
                head_yaw=yaw * 10.0,
                head_pitch=0.0,
            )
        except Exception as exc:
            print(f"Mob spawner render failed at {(te.x, te.y, te.z)}: {exc!r}")
